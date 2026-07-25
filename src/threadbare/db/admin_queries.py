"""Read/write queries for the mod admin page -- deliberately separate from
db/queries.py, which holds only what is safe for any logged-in member
(member-scoped reads, plus each member writing their own read marker). This
module makes privileged writes (`set_channel_indexed`,
`set_channel_visibility_enrolled`) and reads tables that are otherwise
sync-worker-internal (`sync_state`, `worker_heartbeat`), so keeping it apart
makes the mod-only privilege boundary auditable at the module level: every
function here is reachable only through routes gated by web/authz.py's
mod_required.

("entirely read-only" was the original description of db/queries.py; that
stopped being literally true when per-user read markers shipped -- the boundary
this split actually draws is member-safe vs. mod-only, not read vs. write.)
"""

from datetime import UTC, datetime, timedelta

import psycopg

from threadbare.channel_types import NON_CONTENT_TYPES
from threadbare.sync_worker import repository

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
    voice, or stage-voice channel), with its computed visibility, whether
    the bot's own Discord account can currently read it (bot_can_read --
    kept fresh by the sync worker's refresh_channel_bot_access, informational
    only, never gates should_sync itself), mod-controlled indexing flag, and
    backfill checkpoint (if any -- a channel with no sync_state row yet
    hasn't been backfilled).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT
                c.id, c.name, c.type, c.is_public, c.indexed, c.visibility_enrolled,
                c.bot_can_read,
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
    """Delegates to sync_worker/repository.py rather than repeating its SQL.

    This module's usual convention is to keep its own reads, so the mod-only
    privilege boundary stays auditable here -- but that argument doesn't apply
    to a plain site-wide setting the sync worker already reads identically,
    fallback included. Two copies of the `or True` default (see migration 0009)
    meant two places to change it.
    """
    return await repository.get_auto_index_new_channels(conn)


async def set_auto_index_new_channels(conn: psycopg.AsyncConnection, value: bool) -> None:
    await conn.execute(
        """
        INSERT INTO site_settings (id, auto_index_new_channels) VALUES (true, %s)
        ON CONFLICT (id) DO UPDATE SET auto_index_new_channels = EXCLUDED.auto_index_new_channels
        """,
        (value,),
    )


async def insert_custom_theme(
    conn: psycopg.AsyncConnection, *, slug: str, display_name: str
) -> None:
    """Register (or replace) a custom theme's metadata row. The bundle files
    themselves live on the themes volume (web/theme_storage.py); this is only
    the registration record. ON CONFLICT replaces so re-uploading a slug
    updates its display name and bumps updated_at (driving the stylesheet
    cache-buster).
    """
    await conn.execute(
        """
        INSERT INTO custom_themes (slug, display_name) VALUES (%s, %s)
        ON CONFLICT (slug) DO UPDATE
            SET display_name = EXCLUDED.display_name, updated_at = now()
        """,
        (slug, display_name),
    )


async def get_custom_theme(conn: psycopg.AsyncConnection, slug: str) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT slug, display_name, created_at, updated_at FROM custom_themes WHERE slug = %s",
            (slug,),
        )
        return await cur.fetchone()


async def delete_custom_theme(conn: psycopg.AsyncConnection, slug: str) -> None:
    await conn.execute("DELETE FROM custom_themes WHERE slug = %s", (slug,))


async def touch_custom_theme(conn: psycopg.AsyncConnection, slug: str) -> None:
    await conn.execute("UPDATE custom_themes SET updated_at = now() WHERE slug = %s", (slug,))


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
