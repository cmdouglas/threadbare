"""Read/write queries for the mod admin page -- deliberately separate from
db/queries.py, which is entirely read-only and safe for any logged-in
member. This module both writes (`set_channel_indexed`) and reads tables
that are otherwise sync-worker-internal (`sync_state`, `worker_heartbeat`),
so keeping it apart makes the mod-only privilege boundary auditable at the
module level: every function here is reachable only through routes gated by
web/authz.py's mod_required.
"""

from datetime import UTC, datetime, timedelta

import psycopg

from threadbare.channel_types import NON_CONTENT_TYPES

# The sync worker heartbeats every 60s (sync_worker/heartbeat.py); this
# tolerates a few missed beats (transient slowness/GC pauses) before
# flagging a genuinely dead worker. DESIGN.md §9 defers this exact
# comparison to "the future admin page" rather than the sync worker itself.
HEARTBEAT_STALE_THRESHOLD = timedelta(minutes=5)


async def get_channel_indexed(conn: psycopg.AsyncConnection, channel_id: int) -> bool | None:
    async with conn.cursor() as cur:
        await cur.execute("SELECT indexed FROM channels WHERE id = %s", (channel_id,))
        row = await cur.fetchone()
    return row["indexed"] if row else None


async def set_channel_indexed(
    conn: psycopg.AsyncConnection, channel_id: int, indexed: bool
) -> None:
    await conn.execute("UPDATE channels SET indexed = %s WHERE id = %s", (indexed, channel_id))


async def get_channel_visibility_enrolled(
    conn: psycopg.AsyncConnection, channel_id: int
) -> bool | None:
    async with conn.cursor() as cur:
        await cur.execute("SELECT visibility_enrolled FROM channels WHERE id = %s", (channel_id,))
        row = await cur.fetchone()
    return row["visibility_enrolled"] if row else None


async def set_channel_visibility_enrolled(
    conn: psycopg.AsyncConnection, channel_id: int, visibility_enrolled: bool
) -> None:
    await conn.execute(
        "UPDATE channels SET visibility_enrolled = %s WHERE id = %s",
        (visibility_enrolled, channel_id),
    )


async def get_channels_with_sync_state(conn: psycopg.AsyncConnection, guild_id: int) -> list[dict]:
    """Every content-bearing channel in the guild (i.e. not a category,
    voice, or stage-voice channel), with its computed visibility,
    mod-controlled indexing flag, and backfill checkpoint (if any -- a
    channel with no sync_state row yet hasn't been backfilled).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT
                c.id, c.name, c.type, c.is_public, c.indexed, c.visibility_enrolled,
                s.last_backfilled_message_id, s.backfill_complete, s.last_reconciled_at
            FROM channels c
            LEFT JOIN sync_state s ON s.channel_id = c.id
            WHERE c.guild_id = %s AND c.type != ALL(%s)
            ORDER BY c.position, c.name
            """,
            (guild_id, list(NON_CONTENT_TYPES)),
        )
        return await cur.fetchall()


async def get_worker_heartbeat(conn: psycopg.AsyncConnection) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute("SELECT updated_at, last_gateway_event_at FROM worker_heartbeat")
        return await cur.fetchone()


async def get_auto_index_new_channels(conn: psycopg.AsyncConnection) -> bool:
    """Mirrors sync_worker/repository.py's own copy of this read (same
    split-by-module convention as get_channel_indexed vs.
    sync_worker.repository.get_channel_is_public) -- falls back to True
    when no site_settings row exists yet.
    """
    async with conn.cursor() as cur:
        await cur.execute("SELECT auto_index_new_channels FROM site_settings WHERE id = true")
        row = await cur.fetchone()
    return row["auto_index_new_channels"] if row else True


async def set_auto_index_new_channels(conn: psycopg.AsyncConnection, value: bool) -> None:
    await conn.execute(
        """
        INSERT INTO site_settings (id, auto_index_new_channels) VALUES (true, %s)
        ON CONFLICT (id) DO UPDATE SET auto_index_new_channels = EXCLUDED.auto_index_new_channels
        """,
        (value,),
    )


async def get_latest_migration_version(conn: psycopg.AsyncConnection) -> str | None:
    """The most recently applied migration's version string -- the
    concrete way an operator confirms an upgrade's migration step actually
    took effect (paired with threadbare.__version__ on the admin page).
    """
    async with conn.cursor() as cur:
        await cur.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
        row = await cur.fetchone()
    return row["version"] if row else None


def is_heartbeat_stale(heartbeat: dict | None, *, now: datetime | None = None) -> bool:
    """True if the worker has never beaten at all, or hasn't beaten
    recently enough -- the sync worker is presumed dead either way.
    """
    if heartbeat is None:
        return True
    now = now if now is not None else datetime.now(UTC)
    return now - heartbeat["updated_at"] > HEARTBEAT_STALE_THRESHOLD
