"""Channel-roster bootstrap: ensures every non-category channel in a guild
has a `channels` row with correctly computed is_public, so backfill/
reconciliation/live events have something to act on. Threads are out of
scope here, same boundary as the rest of the sync worker (see ROADMAP.md).
"""

import discord

from threadbare.sync_worker import repository
from threadbare.sync_worker.permissions import everyone_overwrite, refresh_channel_public_status


def _row_for(channel, *, guild_id: int) -> dict:
    return {
        "id": channel.id,
        "guild_id": guild_id,
        "parent_id": channel.category_id,
        "type": channel.type.value,
        "name": channel.name,
        "position": channel.position,
        "topic": getattr(channel, "topic", None),
    }


async def discover_channels(client: discord.Client, conn, *, guild_id: int) -> list[int]:
    """Upserts the guild row and every channel's row (including categories —
    channels.parent_id is a self-referencing FK, so a category needs its own
    row for its children to point at, even though it has no content of its
    own), computing is_public for non-category channels via the same
    refresh_channel_public_status used by live CHANNEL_UPDATE/role events —
    so there's exactly one function in the codebase that ever computes
    is_public. Safe to call repeatedly (e.g. on every gateway reconnect):
    metadata updates, is_public/indexed never do. Returns the ids of the
    non-category channels processed.
    """
    guild = client.get_guild(guild_id) or await client.fetch_guild(guild_id)
    await repository.upsert_guild(
        conn,
        {
            "id": guild.id,
            "name": guild.name,
            "icon": guild.icon.key if guild.icon else None,
        },
    )

    channels = await guild.fetch_channels()
    categories = [c for c in channels if c.type is discord.ChannelType.category]
    others = [c for c in channels if c.type is not discord.ChannelType.category]

    # Categories first: a channel's parent_id FK must point at a category
    # row that already exists, and fetch_channels() doesn't guarantee any
    # particular order.
    for category in categories:
        await repository.upsert_channel(conn, _row_for(category, guild_id=guild.id))

    default_role_permissions = guild.default_role.permissions.value
    discovered_ids = []

    for channel in others:
        await repository.upsert_channel(conn, _row_for(channel, guild_id=guild.id))

        category_overwrite = everyone_overwrite(channel.category) if channel.category else None
        await refresh_channel_public_status(
            conn,
            channel_id=channel.id,
            default_role_permissions=default_role_permissions,
            category_overwrite=category_overwrite,
            channel_overwrite=everyone_overwrite(channel),
        )
        discovered_ids.append(channel.id)

    return discovered_ids
