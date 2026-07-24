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
from threadbare.sync_worker.channel_overwrites import sync_channel_overwrites
from threadbare.sync_worker.discord_types import MessageLike, RoleLike, ThreadLike, UserLike
from threadbare.sync_worker.permissions import (
    everyone_overwrite,
    refresh_channel_bot_access,
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


async def handle_member_update(conn, before: UserLike, after: UserLike) -> None:
    """GUILD_MEMBER_UPDATE fires for any member change (roles, timeout,
    pending status, ...), not just a rename -- diffing the two
    user_to_row() projections (rather than reading raw nick/global_name/
    avatar fields) reuses the exact shape upsert_user already writes, so
    "did anything we store change" can't drift from "what do we store", and
    guards against a write on every unrelated update a busy server
    generates constantly.
    """
    before_row = transform.user_to_row(before)
    after_row = transform.user_to_row(after)
    if before_row == after_row:
        return
    await repository.upsert_user(conn, after_row)


async def handle_role_upsert(conn, role: RoleLike, *, guild_id: int) -> None:
    """New or edited role -- keeps the roles table (used for username
    display color, DESIGN.md Phase 2's future permission mirroring) fresh.
    Separate from handle_role_permissions_changed, which recomputes channel
    is_public and has nothing to do with storing role rows.
    """
    await repository.upsert_role(conn, transform.role_to_row(role, guild_id=guild_id))


async def handle_role_delete(conn, role_id: int) -> None:
    await repository.delete_role(conn, role_id)


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
    if flags is None or not should_sync(
        is_public=flags[0], indexed=flags[1], visibility_enrolled=flags[2]
    ):
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


# Voice/stage-voice channels never get a channels row at all -- a stated
# non-goal (DESIGN.md §2), matching discover_channels()'s own exclusion.
_NO_ROW_CHANNEL_TYPES = (discord.ChannelType.voice, discord.ChannelType.stage_voice)


async def handle_channel_upsert(conn, channel: discord.abc.GuildChannel, *, guild_id: int) -> None:
    """New/renamed/moved channel -- keeps name/topic/position fresh without
    waiting for the next discover_channels() pass. Self-heals the parent
    category's row first: a mod can create a category and move a channel
    into it in two separate gateway events, so channel.category may not
    have a channels row yet -- same FK-ordering hazard discover_channels()
    itself hit once (see ROADMAP.md), just live instead of at
    batch-discovery time. No should_sync gating needed -- upsert_channel
    never touches is_public/indexed on conflict, so this is safe to call
    unconditionally, same reasoning as handle_role_upsert.
    """
    if channel.type in _NO_ROW_CHANNEL_TYPES:
        return
    if channel.category is not None:
        await repository.upsert_channel(
            conn, transform.channel_to_row(channel.category, guild_id=guild_id)
        )
    await repository.upsert_channel(conn, transform.channel_to_row(channel, guild_id=guild_id))


async def handle_channel_create(conn, channel: discord.abc.GuildChannel, *, guild_id: int) -> None:
    """New channel: made visible in the admin panel (so a mod can review
    and opt it in) but never auto-indexed/imported -- indexed is forced
    false regardless of the table's normal schema-default-true INSERT (see
    repository.insert_new_channel), a deliberate opt-in gate distinct from
    is_public (computed from permissions, below) and requiring an explicit
    admin action before any content is ever fetched. Same category
    self-heal and voice/stage-voice exclusion as handle_channel_upsert.
    """
    if channel.type in _NO_ROW_CHANNEL_TYPES:
        return
    if channel.category is not None:
        await repository.upsert_channel(
            conn, transform.channel_to_row(channel.category, guild_id=guild_id)
        )
    await repository.insert_new_channel(conn, transform.channel_to_row(channel, guild_id=guild_id))
    # Purely informational for the admin table (is_public is computed from
    # permissions, indexed is the separate mod-controlled import gate
    # above) -- without this the new row would show is_public=false even
    # when the channel is actually publicly readable, which would mislead.
    await handle_channel_permissions_changed(conn, channel)


async def handle_channel_delete(conn, channel_id: int) -> None:
    await repository.delete_channel(conn, channel_id)


async def handle_channel_permissions_changed(conn, channel: discord.abc.GuildChannel) -> None:
    """Fired on CHANNEL_UPDATE (via handle_channel_upsert's caller) and
    CHANNEL_CREATE (handle_channel_create below) -- exactly the two live
    events where a channel's permission overwrites can change. Recomputes
    is_public (the @everyone-only case) and bot_can_read (the bot's own
    access, informational-only for admin.html) and, since Phase 2, also
    re-syncs the full stored role/member overwrite tables to match Discord
    exactly.
    """
    category_overwrite = everyone_overwrite(channel.category) if channel.category else None
    await refresh_channel_public_status(
        conn,
        channel_id=channel.id,
        default_role_permissions=channel.guild.default_role.permissions.value,
        category_overwrite=category_overwrite,
        channel_overwrite=everyone_overwrite(channel),
    )
    await refresh_channel_bot_access(
        conn,
        channel_id=channel.id,
        bot_permissions=channel.permissions_for(channel.guild.me).value,
    )
    await sync_channel_overwrites(conn, channel)


async def handle_role_permissions_changed(conn, guild: discord.Guild) -> None:
    """A role edit/delete doesn't say which channels it affects, so recompute
    every non-category channel in the guild -- including bot_can_read, since
    the edited/deleted role could just as easily be one the bot itself
    holds. Cheap at v1's single-guild, tens-of-channels scale (DESIGN.md
    §7's Phase 2 note on why this doesn't scale to multi-guild, and why
    that's fine for now).
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
        await refresh_channel_bot_access(
            conn,
            channel_id=channel.id,
            bot_permissions=channel.permissions_for(guild.me).value,
        )
