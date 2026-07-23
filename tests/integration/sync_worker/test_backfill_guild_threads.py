import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord

from threadbare.db.pool import create_pool
from threadbare.sync_worker.backfill import backfill_guild_threads


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


@dataclass
class FakeThread:
    id: int
    parent_id: int
    name: str = "a thread"
    archived: bool = False
    created_at: datetime | None = field(default_factory=lambda: datetime.now(UTC))
    message_count: int = 0


class FakeChannel:
    def __init__(self, id, type=discord.ChannelType.text, archived=()):
        self.id = id
        self.type = type
        self._archived = list(archived)

    async def archived_threads(self, *, private=False):
        for thread in self._archived:
            yield thread


class FakeForumChannel(discord.ForumChannel):
    """Shaped like a real ForumChannel for isinstance purposes — its
    archived_threads() takes no private= kwarg at all (forum threads can
    never be private), unlike FakeChannel's TextChannel-shaped fake above.
    Subclassing the real discord.ForumChannel (rather than duck-typing) is
    what lets this test actually exercise the isinstance branch in
    discover_archived_threads() and catch a TypeError if that branch were
    wrong — a plain duck-typed fake accepting `private` unconditionally is
    exactly why this bug wasn't caught before.
    """

    def __init__(self, id, archived=()):
        self.id = id
        self._type = discord.ChannelType.forum.value  # ForumChannel.type is a read-only property
        self._archived = list(archived)

    async def archived_threads(self, *, limit=100, before=None):
        for thread in self._archived:
            yield thread


class FakeGuild:
    def __init__(self, channels, active_threads=()):
        self._channels = channels
        self._active_threads = list(active_threads)

    async def fetch_channels(self):
        return self._channels

    async def active_threads(self):
        return self._active_threads


class FakeClient:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild

    async def fetch_guild(self, guild_id):
        return self._guild


class ThreadKeyedFetcher:
    def __init__(self, pages_by_thread: dict[int, list[FakeMessage]]):
        self._pages_by_thread = pages_by_thread
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
            return self._pages_by_thread.get(channel_id, [])
        self.current -= 1
        return []


class FailingThreadFetcher:
    """Like ThreadKeyedFetcher, but raises for one designated thread --
    tests that backfill_guild_threads() isolates a single thread's crash
    instead of cancelling every other thread via asyncio.gather, mirroring
    backfill_guild()'s own isolation for channels.
    """

    def __init__(self, pages_by_thread: dict[int, list[FakeMessage]], *, failing_thread_id: int):
        self._pages_by_thread = pages_by_thread
        self._failing_thread_id = failing_thread_id
        self.calls: list[int] = []

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls.append(channel_id)
        if channel_id == self._failing_thread_id:
            raise RuntimeError("simulated backfill crash")
        return self._pages_by_thread.get(channel_id, [])


async def _cleanup(conn):
    await conn.execute("DELETE FROM messages")
    await conn.execute("DELETE FROM thread_sync_state")
    await conn.execute("DELETE FROM threads")
    await conn.execute("DELETE FROM sync_state")
    await conn.execute("DELETE FROM channels")
    await conn.execute("DELETE FROM guilds")
    await conn.execute("DELETE FROM users")
    await conn.commit()


async def _seed_channel(conn, *, guild_id, channel_id, is_public, indexed=True, type=0):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (guild_id, "Test Guild"),
    )
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (channel_id, guild_id, type, f"chan-{channel_id}", is_public, indexed),
    )


async def test_backfill_guild_threads_backfills_active_and_archived_threads(
    db_conn, test_database_url
):
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await db_conn.commit()

    active = FakeThread(id=3000, parent_id=10)
    archived = FakeThread(id=3001, parent_id=10, archived=True)
    channel = FakeChannel(id=10, archived=[archived])
    guild = FakeGuild([channel], active_threads=[active])
    client = FakeClient(guild)
    author = FakeAuthor(id=1)
    fetcher = ThreadKeyedFetcher(
        {3000: [FakeMessage(id=100, author=author)], 3001: [FakeMessage(id=101, author=author)]}
    )

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        semaphore = asyncio.Semaphore(3)
        await backfill_guild_threads(
            client,
            pool,
            guild_id=1,
            channels=[channel],
            semaphore=semaphore,
            fetcher=fetcher,
        )
    finally:
        await pool.close()

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM threads ORDER BY id")
        assert {row["id"] for row in await cur.fetchall()} == {3000, 3001}
        await cur.execute("SELECT id FROM messages ORDER BY id")
        assert {row["id"] for row in await cur.fetchall()} == {100, 101}

    await _cleanup(db_conn)


async def test_backfill_guild_threads_skips_threads_of_a_non_public_channel(
    db_conn, test_database_url
):
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=False)
    await db_conn.commit()

    active = FakeThread(id=3000, parent_id=10)
    channel = FakeChannel(id=10)
    guild = FakeGuild([channel], active_threads=[active])
    client = FakeClient(guild)
    fetcher = ThreadKeyedFetcher({})

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        semaphore = asyncio.Semaphore(3)
        await backfill_guild_threads(
            client, pool, guild_id=1, channels=[channel], semaphore=semaphore, fetcher=fetcher
        )
    finally:
        await pool.close()

    assert 3000 not in fetcher.calls
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages")
        assert (await cur.fetchone())["n"] == 0

    await _cleanup(db_conn)


async def test_backfill_guild_threads_discovers_and_backfills_threads_of_a_forum_channel(
    db_conn, test_database_url
):
    # Forum "posts" are just discord.Thread objects parented to a
    # ForumChannel — both active and archived ones should be discovered and
    # backfilled like any other channel's threads, once the parent forum's
    # own is_public is true. Uses FakeForumChannel (a real ForumChannel
    # subclass) rather than a duck-typed fake so archived_threads() actually
    # exercises the isinstance branch discover_archived_threads() needs —
    # ForumChannel.archived_threads() has no private= kwarg at all, unlike
    # TextChannel's, and a duck-typed fake accepting it unconditionally is
    # exactly why this signature mismatch went uncaught before.
    await _seed_channel(
        db_conn,
        guild_id=1,
        channel_id=10,
        is_public=True,
        type=discord.ChannelType.forum.value,
    )
    await db_conn.commit()

    active = FakeThread(id=3000, parent_id=10)
    archived = FakeThread(id=3001, parent_id=10, archived=True)
    forum_channel = FakeForumChannel(id=10, archived=[archived])
    guild = FakeGuild([forum_channel], active_threads=[active])
    client = FakeClient(guild)
    author = FakeAuthor(id=1)
    fetcher = ThreadKeyedFetcher(
        {3000: [FakeMessage(id=100, author=author)], 3001: [FakeMessage(id=101, author=author)]}
    )

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        semaphore = asyncio.Semaphore(3)
        await backfill_guild_threads(
            client,
            pool,
            guild_id=1,
            channels=[forum_channel],
            semaphore=semaphore,
            fetcher=fetcher,
        )
    finally:
        await pool.close()

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM threads ORDER BY id")
        assert {row["id"] for row in await cur.fetchall()} == {3000, 3001}
        await cur.execute("SELECT id FROM messages ORDER BY id")
        assert {row["id"] for row in await cur.fetchall()} == {100, 101}

    await _cleanup(db_conn)


async def test_backfill_guild_threads_respects_the_shared_concurrency_cap(
    db_conn, test_database_url
):
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await db_conn.commit()

    thread_ids = [3000, 3001, 3002, 3003, 3004, 3005]
    active_threads = [FakeThread(id=tid, parent_id=10) for tid in thread_ids]
    channel = FakeChannel(id=10)
    guild = FakeGuild([channel], active_threads=active_threads)
    client = FakeClient(guild)
    author = FakeAuthor(id=1)
    fetcher = ThreadKeyedFetcher(
        {tid: [FakeMessage(id=tid * 10, author=author)] for tid in thread_ids}
    )

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        semaphore = asyncio.Semaphore(2)
        await backfill_guild_threads(
            client, pool, guild_id=1, channels=[channel], semaphore=semaphore, fetcher=fetcher
        )
    finally:
        await pool.close()

    assert fetcher.max_concurrent_seen <= 2

    await _cleanup(db_conn)


async def test_backfill_guild_threads_isolates_a_thread_crash_from_the_rest(
    db_conn, test_database_url, caplog
):
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await db_conn.commit()

    crashing = FakeThread(id=3000, parent_id=10)
    fine = FakeThread(id=3001, parent_id=10)
    channel = FakeChannel(id=10)
    guild = FakeGuild([channel], active_threads=[crashing, fine])
    client = FakeClient(guild)
    author = FakeAuthor(id=1)
    fetcher = FailingThreadFetcher(
        {
            3000: [FakeMessage(id=100, author=author)],
            3001: [FakeMessage(id=101, author=author)],
        },
        failing_thread_id=3000,
    )

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        semaphore = asyncio.Semaphore(3)
        with caplog.at_level(logging.ERROR):
            await backfill_guild_threads(
                client, pool, guild_id=1, channels=[channel], semaphore=semaphore, fetcher=fetcher
            )
    finally:
        await pool.close()

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM messages ORDER BY id")
        # Thread 3000's crash doesn't cancel thread 3001's still-in-flight
        # backfill via asyncio.gather -- its message is written regardless.
        assert {row["id"] for row in await cur.fetchall()} == {101}
    assert "thread 3000" in caplog.text

    await _cleanup(db_conn)
