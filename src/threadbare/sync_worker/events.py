"""Live gateway event handling. Everything here is thin glue: unpack a
discord.py event/payload object and delegate to backfill.RepositoryBackfillSink
(message writes, same upsert path as backfill — an edit is just a write with
the same id), repository.py (deletes), or permissions.py (visibility
recompute). No business logic lives here beyond extracting the raw @everyone
overwrite ints discord.py's channel/category objects hold.
"""

import discord

from threadbare.sync_worker import repository, transform
from threadbare.sync_worker.backfill import RepositoryBackfillSink
from threadbare.sync_worker.discord_types import MessageLike, ThreadLike
from threadbare.sync_worker.permissions import (
    everyone_overwrite,
    refresh_channel_public_status,
    should_sync,
)


async def handle_message_create(
    conn,
    message: MessageLike,
    *,
    channel_id: int | None = None,
    thread_id: int | None = None,
    thread: ThreadLike | None = None,
) -> None:
    # A thread carries no permission overwrites of its own (visibility keys
    # off the parent channel), so its row is just metadata — safe to upsert
    # unconditionally before the message write, same self-healing shape as
    # discover_channels(). Written first, same connection/transaction as the
    # message write, so a crash between the two can't leave messages.thread_id
    # dangling.
    if thread is not None:
        await repository.upsert_thread(conn, transform.thread_to_row(thread))
    await RepositoryBackfillSink(conn).write_message(
        message, channel_id=channel_id, thread_id=thread_id
    )


async def handle_message_edit(
    conn,
    message: MessageLike,
    *,
    channel_id: int | None = None,
    thread_id: int | None = None,
    thread: ThreadLike | None = None,
) -> None:
    # Upserts make this identical to create: the same write path updates
    # content/edited_at on conflict instead of inserting a duplicate.
    await handle_message_create(
        conn, message, channel_id=channel_id, thread_id=thread_id, thread=thread
    )


async def handle_message_delete(conn, message_id: int) -> None:
    await repository.delete_message(conn, message_id)


async def handle_bulk_message_delete(conn, message_ids: list[int]) -> None:
    await repository.delete_messages(conn, message_ids)


async def handle_thread_upsert(conn, thread: ThreadLike) -> None:
    """New/renamed/(un)archived thread. Gated on the parent channel being
    in-scope (should_sync), mirroring discover_active_threads's own gate —
    unlike handle_message_create's unconditional thread upsert (which always
    accompanies a real message write and self-heals via that write's own
    path next time), this can fire with no message ever having been
    written, so it needs its own gate to avoid seeding a row for a thread
    whose parent isn't supposed to be mirrored.
    """
    flags = await repository.get_channel_sync_flags(conn, thread.parent_id)
    if flags is None or not should_sync(is_public=flags[0], indexed=flags[1]):
        return
    await repository.upsert_thread(conn, transform.thread_to_row(thread))


async def handle_thread_delete(conn, thread_id: int) -> None:
    await repository.delete_thread(conn, thread_id)


async def handle_reaction_add(conn, *, message_id: int, emoji: str) -> None:
    """No per-reactor identity is ever read off the payload or stored here —
    aggregate counts only (DESIGN.md §3/§10). Gated on the message already
    being stored, to avoid ForeignKeyViolation for a message this instance
    never saw (outside reconciliation's lookback, or never backfilled).
    """
    if not await repository.message_exists(conn, message_id):
        return
    await repository.increment_reaction(conn, message_id=message_id, emoji=emoji)


async def handle_reaction_remove(conn, *, message_id: int, emoji: str) -> None:
    if not await repository.message_exists(conn, message_id):
        return
    await repository.decrement_reaction(conn, message_id=message_id, emoji=emoji)


async def handle_reaction_clear(conn, message_id: int) -> None:
    await repository.clear_reactions(conn, message_id)


async def handle_reaction_clear_emoji(conn, *, message_id: int, emoji: str) -> None:
    await repository.clear_reaction_emoji(conn, message_id=message_id, emoji=emoji)


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
