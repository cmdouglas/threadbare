"""Checkpointed channel backfill. Orchestration is written against two small
Protocols (HistoryFetcher, BackfillSink) rather than discord.py/psycopg
directly, so the paging/checkpoint/resume logic is unit-testable with
in-memory fakes — no live gateway connection or real database required.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

import discord

from threadbare.sync_worker import repository, transform
from threadbare.sync_worker.checkpoints import advance_backfill_progress
from threadbare.sync_worker.discord_types import MessageLike
from threadbare.sync_worker.discovery import discover_active_threads
from threadbare.sync_worker.permissions import should_sync

SKIPPED_CHANNEL_TYPES = (
    discord.ChannelType.category,
    discord.ChannelType.forum,
    # Voice/stage-voice channels are a stated non-goal (DESIGN.md §2).
    # discover_channels() no longer creates rows for them, but this guards
    # an already-deployed install with a stale row from before that fix.
    discord.ChannelType.voice,
    discord.ChannelType.stage_voice,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100
ATTACHMENT_URL_TTL = timedelta(hours=24)
DEFAULT_MAX_CONCURRENCY = 3
DEFAULT_MAX_RETRIES = 3
SLOW_WAIT_LOG_THRESHOLD_SECONDS = 1.0


class HistoryFetcher(Protocol):
    async def fetch_batch(
        self, *, channel_id: int, after: int | None, limit: int
    ) -> list[MessageLike]: ...


class BackfillSink(Protocol):
    async def get_checkpoint(self, channel_id: int) -> int | None: ...

    async def write_users(self, authors: list) -> None: ...

    async def write_message(
        self, message: MessageLike, *, channel_id: int | None = None, thread_id: int | None = None
    ) -> None: ...

    async def set_checkpoint(
        self, channel_id: int, *, last_message_id: int | None, complete: bool
    ) -> None: ...

    async def get_thread_checkpoint(self, thread_id: int) -> int | None: ...

    async def set_thread_checkpoint(
        self, thread_id: int, *, last_message_id: int | None, complete: bool
    ) -> None: ...

    async def commit(self) -> None: ...


def _authors_sorted_by_id(batch: list[MessageLike]) -> list:
    """Distinct message authors in a batch, sorted ascending by id.

    backfill_guild() runs multiple channels/threads concurrently, each
    holding one open transaction per batch. If two concurrent transactions
    upsert overlapping authors in different orders (message arrival order,
    unrelated to author id), that's a classic Postgres upsert deadlock: txn
    A locks author X then waits on Y (held by B); txn B locks Y then waits
    on X (held by A). Upserting a batch's authors up front, in this same
    fixed order regardless of which transaction runs it, removes the
    possibility of that cycle forming.
    """
    seen = {}
    for message in batch:
        seen.setdefault(message.author.id, message.author)
    return [seen[author_id] for author_id in sorted(seen)]


def _estimate_attachment_url_expiry() -> datetime:
    # Discord's signed CDN URLs expire in ~24h (DESIGN.md §3); we don't parse
    # the signature's exact expiry, just estimate conservatively. Refreshing
    # an expired URL is the web app's /att/{id} proxy job, not the sync
    # worker's — see DESIGN.md §4.
    return datetime.now(UTC) + ATTACHMENT_URL_TTL


class DiscordHistoryFetcher:
    """The real HistoryFetcher, wrapping a discord.py client's channel
    history. discord.Message objects structurally satisfy MessageLike, so no
    adaptation is needed beyond the fetch call itself.
    """

    def __init__(self, client: discord.Client):
        self._client = client

    async def fetch_batch(
        self, *, channel_id: int, after: int | None, limit: int
    ) -> list[MessageLike]:
        channel = self._client.get_channel(channel_id) or await self._client.fetch_channel(
            channel_id
        )
        after_obj = discord.Object(id=after) if after is not None else None
        return [
            message
            async for message in channel.history(after=after_obj, limit=limit, oldest_first=True)
        ]


class BoundedHistoryFetcher:
    """Wraps any HistoryFetcher with a cap on concurrent in-flight fetch
    calls. discord.py's HTTPClient already honors rate-limit headers and
    backs off automatically — this is a different concern, bounding how
    many requests we allow in flight at once so backfilling/reconciling many
    channels concurrently (once a multi-channel orchestrator exists; see
    ROADMAP.md) doesn't outpace discord.py's own bucket prediction.
    """

    def __init__(self, fetcher: HistoryFetcher, *, max_concurrency: int = DEFAULT_MAX_CONCURRENCY):
        self._fetcher = fetcher
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_batch(
        self, *, channel_id: int, after: int | None, limit: int
    ) -> list[MessageLike]:
        async with self._semaphore:
            return await self._fetcher.fetch_batch(channel_id=channel_id, after=after, limit=limit)


class RetryingHistoryFetcher:
    """Wraps any HistoryFetcher, retrying with backoff specifically on
    discord.RateLimited — raised only when discord.py's own internal wait
    would exceed its configured max_ratelimit_timeout, so it gives up and
    surfaces the error rather than blocking forever. Ordinary 429s are
    already handled transparently inside discord.py's HTTPClient; this is
    the fallback for the case it explicitly can't. Any other exception
    propagates immediately, untouched.
    """

    def __init__(self, fetcher: HistoryFetcher, *, max_retries: int = DEFAULT_MAX_RETRIES):
        self._fetcher = fetcher
        self._max_retries = max_retries

    async def fetch_batch(
        self, *, channel_id: int, after: int | None, limit: int
    ) -> list[MessageLike]:
        attempt = 0
        while True:
            try:
                return await self._fetcher.fetch_batch(
                    channel_id=channel_id, after=after, limit=limit
                )
            except discord.RateLimited as e:
                attempt += 1
                if attempt > self._max_retries:
                    raise
                if e.retry_after > SLOW_WAIT_LOG_THRESHOLD_SECONDS:
                    logger.warning(
                        "Rate limited fetching channel %s, waiting %.1fs (attempt %d/%d)",
                        channel_id,
                        e.retry_after,
                        attempt,
                        self._max_retries,
                    )
                await asyncio.sleep(e.retry_after)


class RepositoryBackfillSink:
    """The real BackfillSink, wrapping repository.py against an open
    connection. Callers own the connection's transaction boundary.
    """

    def __init__(self, conn):
        self._conn = conn

    async def get_checkpoint(self, channel_id: int) -> int | None:
        return await repository.get_backfill_checkpoint(self._conn, channel_id)

    async def write_users(self, authors: list) -> None:
        for author in authors:
            await repository.upsert_user(self._conn, transform.user_to_row(author))

    async def write_message(
        self, message: MessageLike, *, channel_id: int | None = None, thread_id: int | None = None
    ) -> None:
        await repository.upsert_user(self._conn, transform.user_to_row(message.author))
        await repository.upsert_message(
            self._conn,
            transform.message_to_row(message, channel_id=channel_id, thread_id=thread_id),
        )
        for attachment in message.attachments:
            await repository.upsert_attachment(
                self._conn,
                transform.attachment_to_row(
                    attachment,
                    message_id=message.id,
                    url_expires_at=_estimate_attachment_url_expiry(),
                ),
            )
        # Self-healing, matching message content: every Message object
        # touched here (backfill, reconciliation, or a live create/edit)
        # carries Discord's current, authoritative reaction counts — sync
        # to match exactly rather than trusting incremental live-event math
        # alone. Reaction gateway events (add/remove/clear) update counts
        # directly for near-real-time speed; this is the drift-correction
        # backstop, not the primary mechanism.
        await repository.sync_message_reactions(
            self._conn, message.id, [(str(r.emoji), r.count) for r in message.reactions]
        )
        # Same self-healing shape as reactions above: every Message object
        # touched here carries Discord's current, authoritative embed set.
        await repository.sync_message_embeds(
            self._conn,
            message.id,
            [
                transform.embed_to_row(embed, message_id=message.id, position=i)
                for i, embed in enumerate(message.embeds)
            ],
        )

    async def set_checkpoint(
        self, channel_id: int, *, last_message_id: int | None, complete: bool
    ) -> None:
        await repository.set_backfill_checkpoint(
            self._conn, channel_id, last_message_id=last_message_id, complete=complete
        )

    async def get_thread_checkpoint(self, thread_id: int) -> int | None:
        return await repository.get_thread_backfill_checkpoint(self._conn, thread_id)

    async def set_thread_checkpoint(
        self, thread_id: int, *, last_message_id: int | None, complete: bool
    ) -> None:
        await repository.set_thread_backfill_checkpoint(
            self._conn, thread_id, last_message_id=last_message_id, complete=complete
        )

    async def commit(self) -> None:
        await self._conn.commit()


async def backfill_channel(
    fetcher: HistoryFetcher,
    sink: BackfillSink,
    *,
    channel_id: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Walk a channel's history forward from its last checkpoint (or the
    beginning, if none), writing messages in batches and persisting a
    checkpoint after each one so a restart resumes near where it left off.
    Writes are idempotent (upserts), so re-processing overlapping messages
    on resume is harmless. Returns the number of messages written.
    """
    after = await sink.get_checkpoint(channel_id)
    total_written = 0

    while True:
        batch = await fetcher.fetch_batch(channel_id=channel_id, after=after, limit=batch_size)

        await sink.write_users(_authors_sorted_by_id(batch))
        for message in batch:
            await sink.write_message(message, channel_id=channel_id)
            total_written += 1

        progress = advance_backfill_progress(
            batch_message_ids=[m.id for m in batch], requested_limit=batch_size
        )
        # An empty final page shouldn't clobber a real checkpoint with None.
        checkpoint = progress.last_message_id if progress.last_message_id is not None else after
        await sink.set_checkpoint(
            channel_id, last_message_id=checkpoint, complete=progress.complete
        )
        await sink.commit()
        after = checkpoint

        if progress.complete:
            break

    return total_written


async def backfill_thread(
    fetcher: HistoryFetcher,
    sink: BackfillSink,
    *,
    thread_id: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Structural twin of backfill_channel() for threads: same paging/
    checkpoint loop, checkpointed against thread_sync_state instead of
    sync_state. Kept as a separate function rather than folding a "kind"
    parameter into backfill_channel() — matches the precedent
    reconcile_channel() already set (a near-identical twin of
    backfill_channel(), not a unified dispatching function). The fetcher's
    channel_id= parameter works unmodified here: DiscordHistoryFetcher just
    resolves whatever id it's given via get_channel/fetch_channel, and a
    discord.py Thread supports .history() identically to a TextChannel.
    """
    after = await sink.get_thread_checkpoint(thread_id)
    total_written = 0

    while True:
        batch = await fetcher.fetch_batch(channel_id=thread_id, after=after, limit=batch_size)

        await sink.write_users(_authors_sorted_by_id(batch))
        for message in batch:
            await sink.write_message(message, thread_id=thread_id)
            total_written += 1

        progress = advance_backfill_progress(
            batch_message_ids=[m.id for m in batch], requested_limit=batch_size
        )
        checkpoint = progress.last_message_id if progress.last_message_id is not None else after
        await sink.set_thread_checkpoint(
            thread_id, last_message_id=checkpoint, complete=progress.complete
        )
        await sink.commit()
        after = checkpoint

        if progress.complete:
            break

    return total_written


async def backfill_guild(
    client: discord.Client,
    pool,
    *,
    guild_id: int,
    max_channel_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    batch_size: int = DEFAULT_BATCH_SIZE,
    fetcher: HistoryFetcher | None = None,
) -> None:
    """Backfill every in-scope channel in a guild, concurrently. Each
    channel holds its own pool connection for the duration of its backfill
    (a RepositoryBackfillSink holds its connection the whole time, not just
    one batch), bounded by max_channel_concurrency — a different resource
    than the Discord-call concurrency BoundedHistoryFetcher caps, which is
    why both exist. Skips categories and forum channels (no top-level
    history; ROADMAP-flagged, not handled here) and anything should_sync
    excludes (indexed, and either public or visibility_enrolled).

    `fetcher` defaults to the real hardened chain
    (Retrying(Bounded(Discord))) built once and shared across all channels,
    so the concurrency cap is real across the whole guild, not per-channel.
    Injectable for testing without a live gateway connection.
    """
    guild = client.get_guild(guild_id) or await client.fetch_guild(guild_id)
    channels = await guild.fetch_channels()
    if fetcher is None:
        fetcher = RetryingHistoryFetcher(BoundedHistoryFetcher(DiscordHistoryFetcher(client)))
    semaphore = asyncio.Semaphore(max_channel_concurrency)

    async def _backfill_one(channel_id: int) -> None:
        async with semaphore, pool.connection() as conn:
            flags = await repository.get_channel_sync_flags(conn, channel_id)
            if flags is None or not should_sync(
                is_public=flags[0], indexed=flags[1], visibility_enrolled=flags[2]
            ):
                return
            sink = RepositoryBackfillSink(conn)
            try:
                await backfill_channel(fetcher, sink, channel_id=channel_id, batch_size=batch_size)
            except Exception:
                # Isolates one channel's failure (e.g. a Postgres deadlock
                # from two channels' transactions upserting overlapping
                # authors -- see _authors_sorted_by_id) from every other
                # channel/thread in this guild's backfill: an un-caught
                # exception here would propagate through the asyncio.gather
                # below and cancel every other in-flight task too. This
                # channel resumes from its last committed checkpoint on the
                # next backfill run.
                logger.exception(
                    "Backfill failed for channel %s -- other channels continue", channel_id
                )

    candidates = [channel.id for channel in channels if channel.type not in SKIPPED_CHANNEL_TYPES]
    await asyncio.gather(
        *(_backfill_one(channel_id) for channel_id in candidates),
        backfill_guild_threads(
            client,
            pool,
            guild_id=guild_id,
            channels=channels,
            semaphore=semaphore,
            fetcher=fetcher,
            batch_size=batch_size,
        ),
    )


async def discover_archived_threads(pool, *, channels: list) -> set[int]:
    """Pure discovery (upsert thread metadata + collect ids), no message
    backfill — the standalone form of what backfill_guild_threads() used to
    do inline, now also reusable by reconcile_guild_threads(): a thread
    created during a gateway outage needs the same archived-thread walk
    regardless of which orchestrator (one-shot startup backfill, or nightly
    recurring reconciliation) eventually finds it.

    Only public archived threads are discoverable here — private archived
    threads the bot hasn't joined require Manage Threads, which the sync
    worker deliberately doesn't request (minimal-permissions design,
    DESIGN.md §3 / ROADMAP.md §7). This is a permanent completeness gap for
    private threads, not a bug.
    """
    thread_ids: set[int] = set()

    async def _collect_for(channel) -> None:
        if channel.type is discord.ChannelType.category or not hasattr(channel, "archived_threads"):
            return
        async with pool.connection() as conn:
            flags = await repository.get_channel_sync_flags(conn, channel.id)
        if flags is None or not should_sync(
            is_public=flags[0], indexed=flags[1], visibility_enrolled=flags[2]
        ):
            return
        # ForumChannel.archived_threads() has no private= kwarg at all (forum
        # threads can never be private) — TextChannel.archived_threads()
        # does. Calling the wrong shape raises TypeError.
        if isinstance(channel, discord.ForumChannel):
            thread_iter = channel.archived_threads()
        else:
            thread_iter = channel.archived_threads(private=False)
        async for thread in thread_iter:
            async with pool.connection() as conn:
                await repository.upsert_thread(conn, transform.thread_to_row(thread))
            thread_ids.add(thread.id)

    await asyncio.gather(*(_collect_for(channel) for channel in channels))
    return thread_ids


async def backfill_guild_threads(
    client: discord.Client,
    pool,
    *,
    guild_id: int,
    channels: list,
    semaphore: asyncio.Semaphore,
    fetcher: HistoryFetcher,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    """Backfills every in-scope thread in a guild: active threads
    (rediscovered here via discover_active_threads — not assumed
    pre-populated by on_ready, so this is self-contained) plus archived
    threads (via discover_archived_threads).

    Shares `semaphore` with channel backfill rather than a separate budget:
    a guild can have far more threads than channels, and two independent
    semaphores could jointly exceed the pool's connection limit at once.
    """
    thread_ids: set[int] = set()

    async with pool.connection() as conn:
        thread_ids.update(await discover_active_threads(client, conn, guild_id=guild_id))
    thread_ids.update(await discover_archived_threads(pool, channels=channels))

    async def _backfill_one(thread_id: int) -> None:
        async with semaphore, pool.connection() as conn:
            sink = RepositoryBackfillSink(conn)
            try:
                await backfill_thread(fetcher, sink, thread_id=thread_id, batch_size=batch_size)
            except Exception:
                # Same isolation rationale as backfill_guild's _backfill_one.
                logger.exception(
                    "Backfill failed for thread %s -- other threads continue", thread_id
                )

    await asyncio.gather(*(_backfill_one(thread_id) for thread_id in thread_ids))
