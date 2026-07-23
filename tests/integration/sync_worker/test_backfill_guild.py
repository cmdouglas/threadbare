import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord

from threadbare.db.pool import create_pool
from threadbare.sync_worker.backfill import backfill_guild


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


class FakeGuild:
    def __init__(self, channels):
        self._channels = channels

    async def fetch_channels(self):
        return self._channels

    async def active_threads(self):
        # backfill_guild() also runs backfill_guild_threads(), which needs
        # this — no threads are relevant to these channel-backfill tests.
        return []


class FakeClient:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild

    async def fetch_guild(self, guild_id):
        return self._guild


class ChannelKeyedFetcher:
    """Serves a fixed page per channel_id, keyed only by channel — enough
    for these tests since each seeded channel gets one small page.
    """

    def __init__(self, pages_by_channel: dict[int, list[FakeMessage]]):
        self._pages_by_channel = pages_by_channel
        self.calls: list[int] = []
        self.current = 0
        self.max_concurrent_seen = 0

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls.append(channel_id)
        self.current += 1
        self.max_concurrent_seen = max(self.max_concurrent_seen, self.current)
        if after is None:
            await asyncio.sleep(0.02)
            self.current -= 1
            return self._pages_by_channel.get(channel_id, [])
        self.current -= 1
        return []


class FailingFetcher:
    """Like ChannelKeyedFetcher, but raises for one designated channel --
    simulates a channel-level crash (e.g. the Postgres deadlock
    _authors_sorted_by_id guards against) to test that backfill_guild()
    isolates it instead of cancelling every other channel via asyncio.gather.
    """

    def __init__(self, pages_by_channel: dict[int, list[FakeMessage]], *, failing_channel_id: int):
        self._pages_by_channel = pages_by_channel
        self._failing_channel_id = failing_channel_id
        self.calls: list[int] = []

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls.append(channel_id)
        if channel_id == self._failing_channel_id:
            raise RuntimeError("simulated backfill crash")
        return self._pages_by_channel.get(channel_id, [])


async def _cleanup(conn):
    # backfill_guild writes through its own pool/connections and commits
    # independently of the db_conn fixture's transaction, so cleanup must be
    # committed explicitly too — otherwise db_conn's rollback-on-teardown
    # would undo the DELETEs but not the already-committed writes, leaking
    # state into later tests.
    await conn.execute("DELETE FROM messages")
    await conn.execute("DELETE FROM sync_state")
    await conn.execute("DELETE FROM channels")
    await conn.execute("DELETE FROM guilds")
    await conn.execute("DELETE FROM users")
    await conn.commit()


async def _seed_channel(conn, *, guild_id, channel_id, is_public, indexed=True):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (guild_id, "Test Guild"),
    )
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, 0, %s, %s, %s)
        """,
        (channel_id, guild_id, f"chan-{channel_id}", is_public, indexed),
    )


async def test_backfill_guild_backfills_only_in_scope_channels(db_conn, test_database_url):
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await _seed_channel(db_conn, guild_id=1, channel_id=11, is_public=False)
    await _seed_channel(db_conn, guild_id=1, channel_id=12, is_public=True, indexed=False)
    await db_conn.commit()  # backfill_guild uses its own pool connections, not db_conn

    author = FakeAuthor(id=1)
    fetcher = ChannelKeyedFetcher(
        {
            10: [FakeMessage(id=100, author=author)],
            11: [FakeMessage(id=101, author=author)],
            12: [FakeMessage(id=102, author=author)],
        }
    )
    guild = FakeGuild([FakeChannel(10), FakeChannel(11), FakeChannel(12)])
    client = FakeClient(guild)

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        await backfill_guild(client, pool, guild_id=1, fetcher=fetcher)
    finally:
        await pool.close()

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM messages ORDER BY id")
        remaining = {row["id"] for row in await cur.fetchall()}
    assert remaining == {100}

    await _cleanup(db_conn)


async def test_backfill_guild_skips_categories_and_forums(db_conn, test_database_url):
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = ChannelKeyedFetcher({10: [FakeMessage(id=100, author=author)]})
    guild = FakeGuild(
        [
            FakeChannel(10),
            FakeChannel(20, type=discord.ChannelType.category),
            FakeChannel(30, type=discord.ChannelType.forum),
        ]
    )
    client = FakeClient(guild)

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        await backfill_guild(client, pool, guild_id=1, fetcher=fetcher)
    finally:
        await pool.close()

    assert 20 not in fetcher.calls
    assert 30 not in fetcher.calls

    await _cleanup(db_conn)


async def test_backfill_guild_skips_voice_and_stage_voice_channels(db_conn, test_database_url):
    # Defense-in-depth: even a stale row from before this exclusion existed
    # (is_public+indexed, as discover_channels() used to compute for any
    # non-category channel) must still not get backfilled.
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await _seed_channel(db_conn, guild_id=1, channel_id=20, is_public=True)
    await _seed_channel(db_conn, guild_id=1, channel_id=30, is_public=True)
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = ChannelKeyedFetcher(
        {
            10: [FakeMessage(id=100, author=author)],
            20: [FakeMessage(id=200, author=author)],
            30: [FakeMessage(id=300, author=author)],
        }
    )
    guild = FakeGuild(
        [
            FakeChannel(10),
            FakeChannel(20, type=discord.ChannelType.voice),
            FakeChannel(30, type=discord.ChannelType.stage_voice),
        ]
    )
    client = FakeClient(guild)

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        await backfill_guild(client, pool, guild_id=1, fetcher=fetcher)
    finally:
        await pool.close()

    assert 20 not in fetcher.calls
    assert 30 not in fetcher.calls

    await _cleanup(db_conn)


async def test_backfill_guild_respects_channel_concurrency_cap(db_conn, test_database_url):
    channel_ids = [10, 11, 12, 13, 14, 15]
    for cid in channel_ids:
        await _seed_channel(db_conn, guild_id=1, channel_id=cid, is_public=True)
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = ChannelKeyedFetcher(
        {cid: [FakeMessage(id=cid * 1000, author=author)] for cid in channel_ids}
    )
    guild = FakeGuild([FakeChannel(cid) for cid in channel_ids])
    client = FakeClient(guild)

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        await backfill_guild(client, pool, guild_id=1, fetcher=fetcher, max_channel_concurrency=2)
    finally:
        await pool.close()

    assert fetcher.max_concurrent_seen <= 2

    await _cleanup(db_conn)


async def test_backfill_guild_isolates_a_channel_crash_from_the_rest(
    db_conn, test_database_url, caplog
):
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await _seed_channel(db_conn, guild_id=1, channel_id=11, is_public=True)
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = FailingFetcher(
        {
            10: [FakeMessage(id=100, author=author)],
            11: [FakeMessage(id=110, author=author)],
        },
        failing_channel_id=10,
    )
    guild = FakeGuild([FakeChannel(10), FakeChannel(11)])
    client = FakeClient(guild)

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        with caplog.at_level(logging.ERROR):
            await backfill_guild(client, pool, guild_id=1, fetcher=fetcher)
    finally:
        await pool.close()

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM messages ORDER BY id")
        # Channel 10's crash doesn't cancel channel 11's still-in-flight
        # backfill via asyncio.gather -- its message is written regardless.
        assert {row["id"] for row in await cur.fetchall()} == {110}
    assert "channel 10" in caplog.text

    await _cleanup(db_conn)
