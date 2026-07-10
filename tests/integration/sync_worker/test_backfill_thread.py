from dataclasses import dataclass, field
from datetime import UTC, datetime

from threadbare.db.pool import create_pool
from threadbare.sync_worker.backfill import RepositoryBackfillSink, backfill_thread


@dataclass
class FakeAuthor:
    id: int
    display_name: str = "someone"
    avatar: object | None = None


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


class FakeFetcher:
    def __init__(self, pages: dict):
        self._pages = pages
        self.calls: list[int | None] = []

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls.append(after)
        return self._pages.get(after, [])


async def _cleanup(conn):
    # backfill_thread commits through its own pool connection, not db_conn,
    # so cleanup must be committed explicitly too — otherwise db_conn's
    # rollback-on-teardown would undo the DELETEs but not the
    # already-committed writes, leaking state into later tests.
    await conn.execute("DELETE FROM messages")
    await conn.execute("DELETE FROM thread_sync_state")
    await conn.execute("DELETE FROM threads")
    await conn.execute("DELETE FROM channels")
    await conn.execute("DELETE FROM guilds")
    await conn.execute("DELETE FROM users")
    await conn.commit()


async def _seed_guild_channel_and_thread(conn, *, guild_id=1, channel_id=10, thread_id=3000):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, 0, 'general', true)
        """,
        (channel_id, guild_id),
    )
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, now())",
        (thread_id, channel_id, "a thread"),
    )


async def test_backfill_thread_writes_messages_with_thread_id_set(db_conn, test_database_url):
    await _seed_guild_channel_and_thread(db_conn)
    await db_conn.commit()  # backfill_thread uses its own pool connection, not db_conn

    author = FakeAuthor(id=1)
    fetcher = FakeFetcher(
        {None: [FakeMessage(id=100, author=author), FakeMessage(id=101, author=author)]}
    )

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        async with pool.connection() as conn:
            sink = RepositoryBackfillSink(conn)
            written = await backfill_thread(fetcher, sink, thread_id=3000, batch_size=100)
    finally:
        await pool.close()

    assert written == 2
    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT channel_id, thread_id FROM messages WHERE id IN (100, 101) ORDER BY id"
        )
        rows = await cur.fetchall()
    assert rows == [
        {"channel_id": None, "thread_id": 3000},
        {"channel_id": None, "thread_id": 3000},
    ]

    await _cleanup(db_conn)


async def test_backfill_thread_checkpoints_land_in_thread_sync_state(db_conn, test_database_url):
    await _seed_guild_channel_and_thread(db_conn)
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = FakeFetcher({None: [FakeMessage(id=100, author=author)]})

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        async with pool.connection() as conn:
            sink = RepositoryBackfillSink(conn)
            await backfill_thread(fetcher, sink, thread_id=3000, batch_size=100)
    finally:
        await pool.close()

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT last_backfilled_message_id, backfill_complete FROM thread_sync_state "
            "WHERE thread_id = 3000"
        )
        row = await cur.fetchone()
    assert row == {"last_backfilled_message_id": 100, "backfill_complete": True}

    await _cleanup(db_conn)


async def test_backfill_thread_resumes_from_persisted_checkpoint_after_restart(
    db_conn, test_database_url
):
    await _seed_guild_channel_and_thread(db_conn)
    await db_conn.commit()

    author = FakeAuthor(id=1)

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        # "First run": only page 1 is available (simulating a crash before
        # the thread's history was exhausted).
        first_fetcher = FakeFetcher(
            {None: [FakeMessage(id=100, author=author), FakeMessage(id=101, author=author)]}
        )
        async with pool.connection() as conn:
            sink = RepositoryBackfillSink(conn)
            await backfill_thread(first_fetcher, sink, thread_id=3000, batch_size=2)

        # "Restart": a fresh connection/sink/fetcher, but the checkpoint
        # committed to Postgres by the first run carries over.
        second_fetcher = FakeFetcher({101: [FakeMessage(id=102, author=author)]})
        async with pool.connection() as conn:
            second_sink = RepositoryBackfillSink(conn)
            written = await backfill_thread(
                second_fetcher, second_sink, thread_id=3000, batch_size=2
            )
    finally:
        await pool.close()

    assert written == 1
    assert second_fetcher.calls[0] == 101
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE thread_id = 3000")
        assert (await cur.fetchone())["n"] == 3

    await _cleanup(db_conn)
