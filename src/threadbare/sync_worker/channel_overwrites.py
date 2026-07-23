"""Persists a channel/category's per-role and per-member permission
overwrites -- the raw data Phase 2's future permission-resolution algorithm
will read (DESIGN.md §7), previously only ever read transiently off live
discord.py objects to compute the single is_public boolean (permissions.py)
and discarded immediately after. Kept separate from permissions.py (which
computes is_public and knows nothing about storing raw overwrite rows) and
transform.py (pure, no I/O) -- one focused module per concern, matching
this codebase's existing convention.
"""

import discord
import psycopg

from threadbare.sync_worker import repository, transform


async def sync_channel_overwrites(
    conn: psycopg.AsyncConnection, channel: discord.abc.GuildChannel
) -> None:
    """Makes channel_role_overwrites/channel_member_overwrites match
    channel.overwrites exactly for this channel (delete-then-bulk-insert,
    via the repository functions below -- an overwrite removed on Discord
    must disappear here too, not just added ones).

    Self-heals a `users` row for any member-tier overwrite target first --
    an overwrite can target a member who has never posted, so
    channel_member_overwrites.user_id's FK would otherwise violate. Same
    self-healing shape as events.handle_channel_upsert's category-parent
    fix.
    """
    for target in channel.overwrites:
        if not isinstance(target, discord.Role):
            await repository.upsert_user(conn, transform.user_to_row(target))

    role_rows, member_rows = transform.channel_overwrite_rows(channel.id, channel.overwrites)
    await repository.sync_channel_role_overwrites(conn, channel.id, role_rows)
    await repository.sync_channel_member_overwrites(conn, channel.id, member_rows)
