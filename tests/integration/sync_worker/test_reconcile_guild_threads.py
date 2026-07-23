from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import discord

from threadbare.db.pool import create_pool
from threadbare.sync_worker.reconciliation import reconcile_guild_threads


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
    """Serves a fixed one-page-per-thread result, keyed by thread id — not
    by `after`, unlike backfill's fakes: reconcile_thread()'s first call
    always passes the lookback cutoff snowflake as `after` (never None), so
    a fake keyed on "after is None" would never match. One page per thread
    is enough here (each thread's page is smaller than the default
    batch_size, so reconcile_thread() completes after a single call).
    """

    def __init__(self, pages_by_thread: dict[int, list[FakeMessage]]):
        self._pages_by_thread = pages_by_thread
        self._served: set[int] = set()
        self.calls: list[int] = []

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls.append(channel_id)
        if channel_id in self._served:
            return []
        self._served.add(channel_id)
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


async def test_reconcile_guild_threads_reconciles_active_and_archived_threads(
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
        await reconcile_guild_threads(
            client,
            pool,
            guild_id=1,
            channels=[channel],
            lookback=timedelta(hours=24),
            fetcher=fetcher,
        )
    finally:
        await pool.close()

    # Assert the real path was exercised: both threads got upserted and reconciled.
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM threads ORDER BY id")
        assert {row["id"] for row in await cur.fetchall()} == {3000, 3001}
        await cur.execute(
            "SELECT thread_id FROM thread_sync_state WHERE last_reconciled_at IS NOT NULL "
            "ORDER BY thread_id"
        )
        assert {row["thread_id"] for row in await cur.fetchall()} == {3000, 3001}
        await cur.execute("SELECT id FROM messages ORDER BY id")
        assert {row["id"] for row in await cur.fetchall()} == {100, 101}

    await _cleanup(db_conn)


async def test_reconcile_guild_threads_skips_threads_of_a_non_public_channel(
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
        await reconcile_guild_threads(client, pool, guild_id=1, channels=[channel], fetcher=fetcher)
    finally:
        await pool.close()

    assert 3000 not in fetcher.calls
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM threads")
        assert (await cur.fetchone())["n"] == 0

    await _cleanup(db_conn)


async def test_reconcile_guild_threads_rediscovers_a_thread_not_seen_since_last_sweep(
    db_conn, test_database_url
):
    """The scenario reconcile_guild_threads exists for: a thread created and
    already archived entirely during a gateway outage — never seen by
    discover_active_threads, never backfilled (backfill_guild_threads only
    runs once, at startup) — must still be found by re-discovering archived
    threads fresh every sweep, not just relying on rows already in Postgres.
    """
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await db_conn.commit()

    archived = FakeThread(id=3002, parent_id=10, archived=True)
    channel = FakeChannel(id=10, archived=[archived])
    guild = FakeGuild([channel], active_threads=[])
    client = FakeClient(guild)
    author = FakeAuthor(id=1)
    fetcher = ThreadKeyedFetcher({3002: [FakeMessage(id=102, author=author)]})

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        await reconcile_guild_threads(client, pool, guild_id=1, channels=[channel], fetcher=fetcher)
    finally:
        await pool.close()

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM threads WHERE id = 3002")
        assert await cur.fetchone() is not None
        await cur.execute("SELECT id FROM messages WHERE thread_id = 3002")
        assert await cur.fetchone() is not None

    await _cleanup(db_conn)
