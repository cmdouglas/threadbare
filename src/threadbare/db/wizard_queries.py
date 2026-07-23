"""Reads/writes for the first-run setup wizard -- separate from
db/admin_queries.py (which is itself already separate from the read-only,
member-safe db/queries.py for the same reason): wizard routes run with no
session/auth at all, an even wider trust boundary than mod-only, so keeping
them in their own module makes that boundary auditable at a glance.
"""

import psycopg

from threadbare.db import admin_queries
from threadbare.sync_worker import repository

_UPDATABLE_WIZARD_STATE_FIELDS = {
    "step",
    "discord_guild_id",
    "discord_client_id",
    "discord_oauth_redirect_uri",
    "channels_confirmed",
    "preflight_results",
}


async def get_or_create_wizard_state(conn: psycopg.AsyncConnection) -> dict:
    async with conn.cursor() as cur:
        await cur.execute("SELECT * FROM wizard_state")
        row = await cur.fetchone()
    if row is not None:
        return row

    async with conn.cursor() as cur:
        await cur.execute("INSERT INTO wizard_state (id) VALUES (true) RETURNING *")
        return await cur.fetchone()


async def update_wizard_state(conn: psycopg.AsyncConnection, **fields) -> None:
    invalid = set(fields) - _UPDATABLE_WIZARD_STATE_FIELDS
    if invalid:
        raise ValueError(f"unknown wizard_state fields: {sorted(invalid)}")
    if not fields:
        return

    set_clause = ", ".join(f"{key} = %({key})s" for key in fields)
    await conn.execute(
        f"UPDATE wizard_state SET {set_clause}, updated_at = now() WHERE id = true", fields
    )


async def seed_guild_and_channels(
    conn: psycopg.AsyncConnection,
    guild_row: dict,
    channel_rows: list[dict],
    *,
    already_confirmed: bool,
) -> None:
    """Reuses sync_worker.repository's own idempotent upserts (that module
    imports only psycopg, not discord.py, so this doesn't reintroduce
    discord.py into the web process) -- the sync worker's own first
    on_ready() will discover and upsert these exact same rows later without
    conflict.

    Forces indexed=false on every channel in the guild when nothing has
    been confirmed yet -- upsert_channel's own schema default
    (indexed=true on fresh insert) is right for the sync worker's
    steady-state philosophy ("index new channels by default") but wrong
    here (DESIGN.md §8.2 requires explicit per-channel opt-in). Once the
    mod has confirmed a selection (already_confirmed=True), a later
    revisit of the channels step (e.g. after a session-loss bounce back to
    /token and forward again) re-syncs channel metadata without wiping the
    prior confirmation back to false.
    """
    await repository.upsert_guild(conn, guild_row)
    for row in channel_rows:
        await repository.upsert_channel(conn, row)

    if not already_confirmed:
        await conn.execute(
            "UPDATE channels SET indexed = false WHERE guild_id = %s", (guild_row["id"],)
        )


async def get_channels_for_guild(conn: psycopg.AsyncConnection, guild_id: int) -> list[dict]:
    """Simpler read than admin_queries.get_channels_with_sync_state -- no
    sync_state join, since nothing has backfilled yet at wizard time.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, name, type, is_public, indexed
            FROM channels
            WHERE guild_id = %s
            ORDER BY position, name
            """,
            (guild_id,),
        )
        return await cur.fetchall()


async def get_auto_index_new_channels(conn: psycopg.AsyncConnection) -> bool:
    """Delegates to admin_queries (same site_settings row the admin page's
    own toggle reads/writes) -- the wizard's channels step offers this same
    setting up front so a mod doesn't have to find the admin page
    separately just to turn it off.
    """
    return await admin_queries.get_auto_index_new_channels(conn)


async def set_auto_index_new_channels(conn: psycopg.AsyncConnection, value: bool) -> None:
    await admin_queries.set_auto_index_new_channels(conn, value)


async def confirm_channel_selection(
    conn: psycopg.AsyncConnection, guild_id: int, indexed_channel_ids: set[int]
) -> None:
    """Full-replace semantics (not just monotonic opt-in): every channel in
    the guild not in `indexed_channel_ids` is explicitly set to
    indexed=False, so unchecking a previously-checked box on a revisit
    works too. Delegates the actual per-row write to
    admin_queries.set_channel_indexed (reused, not duplicated).
    """
    channels = await get_channels_for_guild(conn, guild_id)
    for channel in channels:
        await admin_queries.set_channel_indexed(
            conn, channel["id"], channel["id"] in indexed_channel_ids
        )
