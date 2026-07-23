"""Channel-roster bootstrap: ensures every content-bearing channel in a
guild (i.e. not a category, voice, or stage-voice channel) has a `channels`
row with correctly computed is_public, so backfill/
reconciliation/live events have something to act on. Also discovers active
threads (see discover_active_threads) — archived threads are discovered as
part of backfill instead, since walking them is paginated/comparatively
expensive and there's no reason to separate "discover this archived thread"
from "backfill its messages" into two passes (see ROADMAP.md).
"""

import discord

from threadbare.sync_worker import repository, transform
from threadbare.sync_worker.permissions import (
    everyone_overwrite,
    refresh_channel_public_status,
    should_sync,
)


async def discover_channels(client: discord.Client, conn, *, guild_id: int) -> list[int]:
    """Upserts the guild row and every channel's row (including categories —
    channels.parent_id is a self-referencing FK, so a category needs its own
    row for its children to point at, even though it has no content of its
    own), computing is_public for non-category channels via the same
    refresh_channel_public_status used by live CHANNEL_UPDATE/role events —
    so there's exactly one function in the codebase that ever computes
    is_public. Voice/stage-voice channels get no row at all — a stated
    non-goal (DESIGN.md §2), and unlike categories, nothing parents off
    them. Safe to call repeatedly (e.g. on every gateway reconnect):
    metadata updates, is_public/indexed never do. Returns the ids of the
    content-bearing channels processed.
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
    # Voice/stage-voice channels are a stated non-goal (DESIGN.md §2) and,
    # unlike categories, nothing parents off them -- they get no row at all.
    non_content_types = (
        discord.ChannelType.category,
        discord.ChannelType.voice,
        discord.ChannelType.stage_voice,
    )
    others = [c for c in channels if c.type not in non_content_types]

    # Categories first: a channel's parent_id FK must point at a category
    # row that already exists, and fetch_channels() doesn't guarantee any
    # particular order.
    for category in categories:
        await repository.upsert_channel(conn, transform.channel_to_row(category, guild_id=guild.id))

    default_role_permissions = guild.default_role.permissions.value
    discovered_ids = []

    for channel in others:
        await repository.upsert_channel(conn, transform.channel_to_row(channel, guild_id=guild.id))

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


async def discover_roles(client: discord.Client, conn, *, guild_id: int) -> list[int]:
    """Upserts every guild role's row (id/name/color/position). Fresh REST
    fetch (fetch_roles(), not the cached guild.roles) every call, same
    reasoning as discover_channels' fetch_channels() -- avoids trusting a
    possibly-stale cache. Safe to call repeatedly (e.g. on every gateway
    reconnect): a plain metadata upsert, no computed state to clobber.
    Returns the ids of the roles upserted.
    """
    guild = client.get_guild(guild_id) or await client.fetch_guild(guild_id)
    roles = await guild.fetch_roles()

    discovered_ids = []
    for role in roles:
        await repository.upsert_role(conn, transform.role_to_row(role, guild_id=guild_id))
        discovered_ids.append(role.id)

    return discovered_ids


async def discover_active_threads(client: discord.Client, conn, *, guild_id: int) -> list[int]:
    """Upserts a threads row for every active thread whose parent channel is
    public+indexed — including forum-parented threads, gated the same way as
    any other channel's threads (a forum "post" is just a discord.Thread).
    One non-paginated REST call (Guild.active_threads()) covers every active
    thread the bot's connection can see, public and private, in one shot.
    Run on every on_ready, same as discover_channels: cheap, and — absent
    thread lifecycle live events, deferred until reconciliation covers
    threads — the only mechanism catching a thread created while
    disconnected. Returns the ids of the threads upserted.
    """
    guild = client.get_guild(guild_id) or await client.fetch_guild(guild_id)
    threads = await guild.active_threads()

    discovered_ids = []
    for thread in threads:
        flags = await repository.get_channel_sync_flags(conn, thread.parent_id)
        if flags is None or not should_sync(is_public=flags[0], indexed=flags[1]):
            continue
        await repository.upsert_thread(conn, transform.thread_to_row(thread))
        discovered_ids.append(thread.id)

    return discovered_ids
