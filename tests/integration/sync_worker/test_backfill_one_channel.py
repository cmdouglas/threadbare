from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord

from threadbare.db.pool import create_pool
from threadbare.sync_worker.backfill import backfill_one_channel


@dataclass
class FakeAuthor:
    id: int
    display_name: str = "someone"
    avatar: object | None = None
    bot: bool = False


@dataclass
class FakeMessage:
    id: int
    author: FakeAuthor
    content: str = "hello"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    edited_at: datetime | None = None
    reference: object | None = None
    attachments: list = field(default_factory=list)
    reactions: list = field(default_factory=list)
    embeds: list = field(default_factory=list)


class FakeChannel:
    def __init__(self, id, type=discord.ChannelType.text):
        self.id = id
        self.type = type


class FakeClient:
    """Resolves channel_id -> FakeChannel the same way ThreadbareClient does
    (get_channel first, fetch_channel as a fallback) -- backfill_one_channel
    uses this to decide whether a channel has top-level history at all.
    """

    def __init__(self, channel: FakeChannel):
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel if channel_id == self._channel.id else None

    async def fetch_channel(self, channel_id):
        return self._channel


class ChannelKeyedFetcher:
    def __init__(self, pages_by_channel: dict[int, list]):
        self._pages_by_channel = pages_by_channel
        self.calls: list[int] = []

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls.append(channel_id)
        if after is not None:
            return []
        return self._pages_by_channel.get(channel_id, [])


async def _cleanup(conn):
    # backfill_one_channel writes through its own pool connection, not
    # db_conn, so cleanup must be committed explicitly too -- otherwise
    # db_conn's rollback-on-teardown would undo the DELETEs but not the
    # already-committed writes, leaking state into later tests.
    await conn.execute("DELETE FROM messages")
    await conn.execute("DELETE FROM thread_sync_state")
    await conn.execute("DELETE FROM threads")
    await conn.execute("DELETE FROM sync_state")
    await conn.execute("DELETE FROM channels")
    await conn.execute("DELETE FROM guilds")
    await conn.execute("DELETE FROM users")
    await conn.commit()


async def _seed_guild_and_channel(conn, *, guild_id, channel_id, channel_type: int, thread_id=None):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, %s, 'general', true)
        """,
        (channel_id, guild_id, channel_type),
    )
    if thread_id is not None:
        await conn.execute(
            "INSERT INTO threads (id, parent_channel_id, name, created_at) "
            "VALUES (%s, %s, %s, now())",
            (thread_id, channel_id, "a thread"),
        )


async def test_backfill_one_channel_skips_top_level_history_for_a_forum_channel(
    db_conn, test_database_url
):
    await _seed_guild_and_channel(
        db_conn, guild_id=1, channel_id=10, channel_type=15, thread_id=3000
    )
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = ChannelKeyedFetcher({3000: [FakeMessage(id=100, author=author)]})
    client = FakeClient(FakeChannel(10, type=discord.ChannelType.forum))

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        # In production, walking channel 10's top-level history would call
        # discord.py's ForumChannel.history() -- which doesn't exist, the
        # reported crash. fetcher is a fake HistoryFetcher, not the real
        # DiscordHistoryFetcher, so the regression check here is the
        # assertion below: channel 10 must never reach fetch_batch() at all.
        await backfill_one_channel(client, pool, channel_id=10, fetcher=fetcher)
    finally:
        await pool.close()

    assert 10 not in fetcher.calls
    assert fetcher.calls == [3000]
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE thread_id = 3000")
        assert (await cur.fetchone())["n"] == 1

    await _cleanup(db_conn)


async def test_backfill_one_channel_skips_top_level_history_for_a_media_channel(
    db_conn, test_database_url
):
    await _seed_guild_and_channel(
        db_conn, guild_id=2, channel_id=11, channel_type=16, thread_id=3001
    )
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = ChannelKeyedFetcher({3001: [FakeMessage(id=200, author=author)]})
    client = FakeClient(FakeChannel(11, type=discord.ChannelType.media))

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        await backfill_one_channel(client, pool, channel_id=11, fetcher=fetcher)
    finally:
        await pool.close()

    assert 11 not in fetcher.calls
    assert fetcher.calls == [3001]
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE thread_id = 3001")
        assert (await cur.fetchone())["n"] == 1

    await _cleanup(db_conn)


async def test_backfill_one_channel_still_walks_top_level_history_for_a_text_channel(
    db_conn, test_database_url
):
    await _seed_guild_and_channel(db_conn, guild_id=3, channel_id=12, channel_type=0)
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = ChannelKeyedFetcher({12: [FakeMessage(id=300, author=author)]})
    client = FakeClient(FakeChannel(12, type=discord.ChannelType.text))

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        await backfill_one_channel(client, pool, channel_id=12, fetcher=fetcher)
    finally:
        await pool.close()

    assert fetcher.calls == [12]
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 12")
        assert (await cur.fetchone())["n"] == 1

    await _cleanup(db_conn)
