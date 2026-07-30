"""Mod-triggered maintenance jobs, and the queue that carries them from the
web app to the sync worker.

These are two separate processes with no IPC -- ROADMAP.md §6 records that as
exactly why "trigger re-backfill from the admin page" was deferred rather than
rushed. This is that plumbing: the admin page inserts a row, the worker claims
it on a timer with SELECT ... FOR UPDATE SKIP LOCKED, and the same table
carries the job's status back for the page to display.

Polling rather than LISTEN/NOTIFY deliberately: no persistent listener
connection to babysit, no missed-notification edge case on reconnect, and one
fewer moving part (DESIGN.md §9). A mod waiting up to a tick for an operation
that then runs for minutes does not need millisecond dispatch.
"""

import asyncio
import logging
from datetime import timedelta

import psycopg

from threadbare.db import grouping
from threadbare.sync_worker import repository

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = timedelta(seconds=15)

REGROUP = "regroup"
RESYNC = "resync"


async def enqueue(
    conn: psycopg.AsyncConnection,
    *,
    kind: str,
    channel_id: int | None,
    requested_by: int | None = None,
) -> int | None:
    """Queue a job and return its id, or None if one for the same kind+target
    is already pending. channel_id=None targets every content channel,
    matching the CLI's --reset-all-channels / --regroup-all forms.

    ON CONFLICT DO NOTHING rather than letting the unique violation raise:
    a raised constraint error aborts the whole transaction, so the caller
    couldn't simply swallow it and carry on -- the request's own commit would
    then fail too. Returning None keeps "already queued" an ordinary answer
    instead of an error to recover from.

    The conflict target repeats migration 0018's index exactly, NULLS NOT
    DISTINCT included, so a guild-wide job (channel_id NULL) collides with
    itself rather than queueing a second full pass.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO sync_jobs (kind, channel_id, requested_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (kind, channel_id) WHERE finished_at IS NULL DO NOTHING
            RETURNING id
            """,
            (kind, channel_id, requested_by),
        )
        row = await cur.fetchone()
        return row["id"] if row else None


async def claim_next(conn: psycopg.AsyncConnection) -> dict | None:
    """Take the oldest unstarted job, marking it started. None when idle.

    FOR UPDATE SKIP LOCKED so a second worker (a rolling deploy, or someone
    running two containers by accident) picks a different row rather than
    blocking on this one or, worse, running the same expensive re-walk twice.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE sync_jobs SET started_at = now()
             WHERE id = (
                 SELECT id FROM sync_jobs
                  WHERE started_at IS NULL AND finished_at IS NULL
                  ORDER BY requested_at, id
                  LIMIT 1
                  FOR UPDATE SKIP LOCKED
             )
             RETURNING id, kind, channel_id, requested_by, requested_at, started_at
            """
        )
        return await cur.fetchone()


async def finish(conn: psycopg.AsyncConnection, job_id: int, *, error: str | None = None) -> None:
    """Stamp a job finished, recording `error` if it failed. A failed job is
    finished, never retried automatically: silently re-running an expensive
    history re-walk forever is worse than showing a mod what broke.
    """
    await conn.execute(
        "UPDATE sync_jobs SET finished_at = now(), error = %s WHERE id = %s", (error, job_id)
    )


async def recent(conn: psycopg.AsyncConnection, *, limit: int = 10) -> list[dict]:
    """Newest-first job history for the admin page, with each job's channel
    name resolved (NULL for a guild-wide job).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT j.id, j.kind, j.channel_id, j.requested_by, j.requested_at,
                   j.started_at, j.finished_at, j.error, c.name AS channel_name
              FROM sync_jobs j
              LEFT JOIN channels c ON c.id = j.channel_id
             ORDER BY j.requested_at DESC, j.id DESC
             LIMIT %s
            """,
            (limit,),
        )
        return await cur.fetchall()


async def pending_targets(conn: psycopg.AsyncConnection) -> set[tuple[str, int | None]]:
    """(kind, channel_id) pairs with a job already queued or running -- what
    the admin page disables its buttons from, so a mod sees "queued" instead
    of a constraint violation.
    """
    async with conn.cursor() as cur:
        await cur.execute("SELECT kind, channel_id FROM sync_jobs WHERE finished_at IS NULL")
        return {(row["kind"], row["channel_id"]) for row in await cur.fetchall()}


async def _target_channel_ids(conn: psycopg.AsyncConnection, channel_id: int | None) -> list[int]:
    if channel_id is not None:
        return [channel_id]
    return await repository.get_content_channel_ids(conn)


async def _for_each_channel(pool, channel_ids: list[int], work) -> list[tuple[int, Exception]]:
    """Run `work(channel_id)` over each channel, isolating failures, and return
    the ones that failed.

    Isolation matters more here than anywhere else this pattern appears
    (backfill_guild's _backfill_one, reconcile_guild's loop, regroup_stale_channels):
    a guild-wide job runs over *every* channel, and the most common failure by
    far is a single channel the bot can't read -- 403 Missing Access, which
    `bot_can_read` records as informational precisely because it's expected and
    self-healing. Letting the first one propagate abandons every channel after
    it, and a mod pressing Resync has no way to tell that only some of their
    server was actually resynced.
    """
    failures: list[tuple[int, Exception]] = []
    for channel_id in channel_ids:
        try:
            await work(channel_id)
        except Exception as exc:
            logger.exception("Sync job step failed for channel %s -- others continue", channel_id)
            failures.append((channel_id, exc))
    return failures


async def _describe_failures(pool, failures: list[tuple[int, Exception]], *, total: int) -> str:
    """A job's `error` text: how many channels failed, and which ones by name.

    Discord's own message ("403 Forbidden (error code: 50001): Missing Access")
    names no channel, so on its own it tells a mod nothing actionable -- and
    the job list is exactly where they look to find out what to go fix.
    """
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id, name FROM channels WHERE id = ANY(%s)",
            ([cid for cid, _ in failures],),
        )
        names = {row["id"]: row["name"] for row in await cur.fetchall()}

    # str(exc) is empty for some exception types, which would otherwise render
    # as a bare "#general: " and read as a truncated message rather than an
    # error class the mod can search for. Note this tests `str(exc)`, not
    # `exc`: an exception object is always truthy, so `exc or ...` would never
    # reach the fallback.
    detail = "; ".join(
        f"#{names.get(cid, cid)}: {str(exc) or exc.__class__.__name__}" for cid, exc in failures
    )
    return f"{len(failures)} of {total} channel(s) failed -- {detail}"


async def run_regroup(pool, *, channel_id: int | None) -> list[tuple[int, Exception]]:
    """Recompute post grouping for the target channel(s) and stamp each one
    current. One transaction per channel, matching reconciliation's own sweep:
    a large channel shouldn't hold a transaction open across every other, and
    a failure part-way leaves the channels already stamped alone.
    """
    async with pool.connection() as conn:
        channel_ids = await _target_channel_ids(conn, channel_id)
        generation = await repository.get_grouping_generation(conn)
        gap_seconds = await repository.get_merge_gap_seconds(conn)

    async def _regroup_one(cid: int) -> None:
        async with pool.connection() as conn:
            changed = await grouping.regroup_channel_and_threads(
                conn, channel_id=cid, gap_seconds=gap_seconds
            )
            await repository.stamp_grouping_generation(conn, cid, generation=generation)
            logger.info("Regrouped channel %s (%s messages changed)", cid, changed)

    return await _for_each_channel(pool, channel_ids, _regroup_one)


async def run_resync(pool, *, channel_id: int | None, backfill) -> list[tuple[int, Exception]]:
    """Clear the target channel(s)' checkpoints and re-walk their history.

    The CLI's --reset-channel tells you to restart the worker afterwards, and
    that's only true because backfill runs once at on_ready. A job handler
    owning the reset can simply do the walk itself, so the admin button needs
    no restart. `backfill` is injected (an async callable taking a channel id)
    so this is testable without a live gateway, matching how reconcile_guild
    already takes an injectable fetcher.

    The checkpoint is cleared per channel immediately before that channel's
    walk, not for all of them up front: a channel whose walk then fails has
    already lost its checkpoint, and clearing every checkpoint before the first
    walk would mean one unreadable channel reset the whole guild's progress.
    """
    async with pool.connection() as conn:
        channel_ids = await _target_channel_ids(conn, channel_id)

    async def _resync_one(cid: int) -> None:
        async with pool.connection() as conn:
            await repository.set_backfill_checkpoint(
                conn, cid, last_message_id=None, complete=False
            )
            await repository.reset_thread_checkpoints_for_channel(conn, cid)
        logger.info("Resync: cleared checkpoint for channel %s, re-walking", cid)
        await backfill(cid)

    return await _for_each_channel(pool, channel_ids, _resync_one)


async def run_one(pool, job: dict, *, backfill) -> str | None:
    """Run a job, returning its `error` text or None if every channel
    succeeded. Partial failure is a real, expected outcome -- one unreadable
    channel out of forty -- so it's a returned summary rather than a raised
    exception, which would throw away the fact that the other thirty-nine
    worked.
    """
    if job["kind"] == REGROUP:
        failures = await run_regroup(pool, channel_id=job["channel_id"])
    elif job["kind"] == RESYNC:
        failures = await run_resync(pool, channel_id=job["channel_id"], backfill=backfill)
    else:  # unreachable while the CHECK constraint holds
        return f"Unknown job kind: {job['kind']!r}"

    if not failures:
        return None
    async with pool.connection() as conn:
        total = len(await _target_channel_ids(conn, job["channel_id"]))
    return await _describe_failures(pool, failures, total=total)


async def job_loop(pool, *, backfill, interval: timedelta = DEFAULT_POLL_INTERVAL) -> None:
    """Runs forever, draining the queue then sleeping. Structural twin of
    heartbeat_loop.

    A failing job is recorded and the loop continues -- same isolation
    reconcile_guild applies per channel, and with the same extra bite: an
    uncaught exception would propagate out of this `while True`, and bot.py
    only creates the task when it's None, so one bad job would silently end
    *all* mod-triggered maintenance for the life of the process.
    """
    while True:
        while True:
            async with pool.connection() as conn:
                job = await claim_next(conn)
            if job is None:
                break
            error = None
            try:
                # Per-channel failures come back as a summary rather than an
                # exception (see run_one); this catch is for the job failing
                # outright -- the database going away mid-run, say.
                error = await run_one(pool, job, backfill=backfill)
            except Exception as exc:
                logger.exception("Sync job %s (%s) failed", job["id"], job["kind"])
                error = str(exc) or exc.__class__.__name__
            async with pool.connection() as conn:
                await finish(conn, job["id"], error=error)
        await asyncio.sleep(interval.total_seconds())
