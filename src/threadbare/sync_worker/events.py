"""Live gateway event handling. Everything here is thin glue: unpack a
discord.py event/payload object and delegate to backfill.RepositoryBackfillSink
(message writes, same upsert path as backfill — an edit is just a write with
the same id), repository.py (deletes), or permissions.py (visibility
recompute). No business logic lives here beyond extracting the raw @everyone
overwrite ints discord.py's channel/category objects hold.
"""

import discord

from threadbare.sync_worker import repository
from threadbare.sync_worker.backfill import RepositoryBackfillSink
from threadbare.sync_worker.discord_types import MessageLike
from threadbare.sync_worker.permissions import everyone_overwrite, refresh_channel_public_status


async def handle_message_create(
    conn, message: MessageLike, *, channel_id: int | None = None, thread_id: int | None = None
) -> None:
    await RepositoryBackfillSink(conn).write_message(
        message, channel_id=channel_id, thread_id=thread_id
    )


async def handle_message_edit(
    conn, message: MessageLike, *, channel_id: int | None = None, thread_id: int | None = None
) -> None:
    # Upserts make this identical to create: the same write path updates
    # content/edited_at on conflict instead of inserting a duplicate.
    await handle_message_create(conn, message, channel_id=channel_id, thread_id=thread_id)


async def handle_message_delete(conn, message_id: int) -> None:
    await repository.delete_message(conn, message_id)


async def handle_bulk_message_delete(conn, message_ids: list[int]) -> None:
    await repository.delete_messages(conn, message_ids)


async def handle_channel_permissions_changed(conn, channel: discord.abc.GuildChannel) -> None:
    category_overwrite = everyone_overwrite(channel.category) if channel.category else None
    await refresh_channel_public_status(
        conn,
        channel_id=channel.id,
        default_role_permissions=channel.guild.default_role.permissions.value,
        category_overwrite=category_overwrite,
        channel_overwrite=everyone_overwrite(channel),
    )


async def handle_role_permissions_changed(conn, guild: discord.Guild) -> None:
    """A role edit/delete doesn't say which channels it affects, so recompute
    every non-category channel in the guild. Cheap at v1's single-guild,
    tens-of-channels scale (DESIGN.md §7's Phase 2 note on why this doesn't
    scale to multi-guild, and why that's fine for now).
    """
    default_role_permissions = guild.default_role.permissions.value
    for channel in guild.channels:
        if channel.type is discord.ChannelType.category:
            continue
        category_overwrite = everyone_overwrite(channel.category) if channel.category else None
        await refresh_channel_public_status(
            conn,
            channel_id=channel.id,
            default_role_permissions=default_role_permissions,
            category_overwrite=category_overwrite,
            channel_overwrite=everyone_overwrite(channel),
        )
