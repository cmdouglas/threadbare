import discord
from psycopg_pool import AsyncConnectionPool

from threadbare.sync_worker import events


def _container_ids(channel) -> tuple[int | None, int | None]:
    if isinstance(channel, discord.Thread):
        return None, channel.id
    return channel.id, None


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
        super().__init__(intents=intents, **kwargs)
        self.guild_id = guild_id
        self.pool = pool

    async def on_message(self, message: discord.Message) -> None:
        if self.pool is None or message.guild is None or message.guild.id != self.guild_id:
            return
        channel_id, thread_id = _container_ids(message.channel)
        async with self.pool.connection() as conn:
            await events.handle_message_create(
                conn, message, channel_id=channel_id, thread_id=thread_id
            )

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
        channel_id, thread_id = _container_ids(message.channel)
        async with self.pool.connection() as conn:
            await events.handle_message_edit(
                conn, message, channel_id=channel_id, thread_id=thread_id
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

    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        if self.pool is None or after.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_channel_permissions_changed(conn, after)

    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if self.pool is None or after.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_role_permissions_changed(conn, after.guild)

    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if self.pool is None or role.guild.id != self.guild_id:
            return
        async with self.pool.connection() as conn:
            await events.handle_role_permissions_changed(conn, role.guild)
