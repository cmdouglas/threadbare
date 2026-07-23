import asyncio
import logging
from datetime import UTC, datetime
from functools import partial

import discord
from psycopg_pool import AsyncConnectionPool

from threadbare.sync_worker import events
from threadbare.sync_worker.backfill import backfill_guild
from threadbare.sync_worker.discovery import discover_active_threads, discover_channels, discover_roles
from threadbare.sync_worker.heartbeat import heartbeat_loop
from threadbare.sync_worker.reconciliation import reconciliation_loop

logger = logging.getLogger(__name__)


def _log_if_failed(task: asyncio.Task, *, name: str) -> None:
    """Attached via add_done_callback to each of on_ready's three
    fire-and-forget background loops (backfill/reconciliation/heartbeat) --
    none of discord.py's own exception handling covers a bare
    asyncio.create_task the way it covers gateway event dispatch (on_error),
    so without this a crashed loop would otherwise go unnoticed until the
    whole process exits (asyncio only logs an orphaned task's exception at
    garbage-collection time, and self._backfill_task etc. keep it referenced
    for the client's entire lifetime).
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("%s task crashed", name, exc_info=exc)


async def _resolve_container(
    client: discord.Client, channel
) -> tuple[int | None, int | None, discord.Thread | None]:
    """Maps a discord.py channel/thread object to (channel_id, thread_id,
    thread) for write_message/upsert_thread. Threads carry no permission
    overwrites of their own — the Thread object is only needed for its
    metadata (name/archived/created_at), not for any visibility computation.

    A cold cache (the container was never seen via GUILD_CREATE/THREAD_CREATE)
    hands back a bare PartialMessageable with type=None instead of a real
    Thread/TextChannel — ambiguous as to which it actually is, since a plain
    channel and an uncached thread look identical at that point. Resolved
    with one REST call in that case; cheap in practice since it only hits the
    genuinely-uncached path, not the common case.
    """
    if isinstance(channel, discord.Thread):
        return None, channel.id, channel
    if getattr(channel, "type", None) is None:
        resolved = await client.fetch_channel(channel.id)
        if isinstance(resolved, discord.Thread):
            return None, resolved.id, resolved
        return resolved.id, None, None
    return channel.id, None, None


class ThreadbareClient(discord.Client):
    """Thin glue only: unpacks discord.py objects and delegates to plain,
    dependency-injected functions elsewhere in this package (events.py,
    backfill.py). No business logic belongs in this class — see
    DEVELOPMENT.md / the sync worker plan for why (testability without a
    live gateway).

    `pool` is optional so tests that only need a bare login (no DB writes)
    can construct a client without one; event handlers become no-ops when
    it's unset.
    """

    def __init__(self, *, guild_id: int, pool: AsyncConnectionPool | None = None, **kwargs):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        intents.guild_reactions = True
        # Privileged GUILD_MEMBERS intent -- needs "Server Members Intent"
        # enabled on the Bot tab (docs/DEVELOPMENT.md, wizard_intro.html).
        # Requested solely so on_member_update can keep users.display_name
        # fresh for a renamed member who never posts again (ROADMAP.md).
        intents.members = True
        super().__init__(intents=intents, **kwargs)
        self.guild_id = guild_id
        self.pool = pool
        self.last_gateway_event_at: datetime | None = None
        self._backfill_task: asyncio.Task | None = None
        self._reconciliation_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

    async def on_ready(self) -> None:
        if self.pool is None:
            return

        # Runs every time (including reconnects), not guarded — cheap (one
        # fetch_channels() call plus a few upserts) and self-healing for
        # channels created while briefly disconnected. Reconciliation's
        # first pass needs these rows to already exist, so this is awaited
        # directly rather than backgrounded.
        async with self.pool.connection() as conn:
            await discover_channels(self, conn, guild_id=self.guild_id)
            # Active-thread discovery needs channel rows to already exist
            # (it looks up each thread's parent's sync flags), so it runs
            # after, same connection/transaction.
            await discover_active_threads(self, conn, guild_id=self.guild_id)
            # No ordering dependency with the above -- roles are unrelated
            # to channels/threads.
            await discover_roles(self, conn, guild_id=self.guild_id)

        # The rest are guarded against re-firing on reconnects — these loops
        # already run forever once started.
        if self._backfill_task is None:
            self._backfill_task = asyncio.create_task(
                backfill_guild(self, self.pool, guild_id=self.guild_id)
            )
            self._backfill_task.add_done_callback(partial(_log_if_failed, name="backfill"))
        if self._reconciliation_task is None:
            self._reconciliation_task = asyncio.create_task(
                reconciliation_loop(self, self.pool, guild_id=self.guild_id)
            )
            self._reconciliation_task.add_done_callback(
                partial(_log_if_failed, name="reconciliation")
            )
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(
                heartbeat_loop(
                    self.pool, get_last_gateway_event_at=lambda: self.last_gateway_event_at
                )
            )
            self._heartbeat_task.add_done_callback(partial(_log_if_failed, name="heartbeat"))

    async def on_socket_event_type(self, event_type: str) -> None:
        self.last_gateway_event_at = datetime.now(UTC)

    async def on_message(self, message: discord.Message) -> None:
        if self.pool is None or message.guild is None or message.guild.id != self.guild_id:
            return
        channel_id, thread_id, thread = await _resolve_container(self, message.channel)
        async with self.pool.connection() as conn:
            await events.handle_message_create(
                conn, message, channel_id=channel_id, thread_id=thread_id, thread=thread
            )

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # No raw variant exists for this event (unlike message/thread/
        # reaction events) -- discord.py has no RawMemberUpdateEvent, so the
        # cooked handler is the only option here.
        if self.pool is None or after.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_member_update(conn, before, after)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        message = payload.cached_message
        if message is None:
            channel = self.get_channel(payload.channel_id) or await self.fetch_channel(
                payload.channel_id
            )
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                return
        channel_id, thread_id, thread = await _resolve_container(self, message.channel)
        async with self.pool.connection() as conn:
            await events.handle_message_edit(
                conn, message, channel_id=channel_id, thread_id=thread_id, thread=thread
            )

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_message_delete(conn, payload.message_id)

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_bulk_message_delete(conn, list(payload.message_ids))

    async def on_thread_create(self, thread: discord.Thread) -> None:
        # Reliable for genuine new threads (fires only when THREAD_CREATE's
        # newly_created flag is set) — unlike update/delete, there's no
        # cached-vs-uncached ambiguity here since this is the first time the
        # client learns of the thread at all.
        if self.pool is None or thread.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_thread_upsert(conn, thread)

    async def on_raw_thread_update(self, payload: discord.RawThreadUpdateEvent) -> None:
        # The raw variant always fires, even for threads discord.py hasn't
        # cached — the cooked on_thread_update doesn't (same reasoning as
        # using on_raw_message_edit over on_message_edit elsewhere).
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        thread = payload.thread
        if thread is None:
            thread = await self.fetch_channel(payload.thread_id)
        async with self.pool.connection() as conn:
            await events.handle_thread_upsert(conn, thread)

    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        # The raw variant always fires; the cooked on_thread_delete only
        # fires for already-cached threads — exactly the unreliability
        # ROADMAP.md flags for thread deletes.
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_thread_delete(conn, payload.thread_id)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_reaction_add(
                conn, message_id=payload.message_id, emoji=str(payload.emoji)
            )

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_reaction_remove(
                conn, message_id=payload.message_id, emoji=str(payload.emoji)
            )

    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_reaction_clear(conn, payload.message_id)

    async def on_raw_reaction_clear_emoji(
        self, payload: discord.RawReactionClearEmojiEvent
    ) -> None:
        if self.pool is None or payload.guild_id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_reaction_clear_emoji(
                conn, message_id=payload.message_id, emoji=str(payload.emoji)
            )

    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        if self.pool is None or after.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_channel_permissions_changed(conn, after)

    async def on_guild_role_create(self, role: discord.Role) -> None:
        if self.pool is None or role.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_role_upsert(conn, role, guild_id=self.guild_id)

    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if self.pool is None or after.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_role_upsert(conn, after, guild_id=self.guild_id)
            await events.handle_role_permissions_changed(conn, after.guild)

    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if self.pool is None or role.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_role_delete(conn, role.id)
            await events.handle_role_permissions_changed(conn, role.guild)
