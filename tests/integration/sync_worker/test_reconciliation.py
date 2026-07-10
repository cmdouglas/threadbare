from dataclasses import dataclass, field
from datetime import UTC, datetime

from threadbare.db.pool import create_pool
from threadbare.sync_worker.reconciliation import RepositoryReconciliationSink, reconcile_channel


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


class FakeFetcher:
    def __init__(self, pages: dict):
        self._pages = pages

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        return self._pages.get(after, [])


async def _cleanup(conn):
    # reconcile_channel now commits through its own pool connection, not
    # db_conn, so cleanup must be committed explicitly too — otherwise
    # db_conn's rollback-on-teardown would undo the DELETEs but not the
    # already-committed writes, leaking state into later tests.
    await conn.execute("DELETE FROM messages")
    await conn.execute("DELETE FROM sync_state")
    await conn.execute("DELETE FROM channels")
    await conn.execute("DELETE FROM guilds")
    await conn.execute("DELETE FROM users")
    await conn.commit()


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, 0, 'general', true)
        """,
        (channel_id, guild_id),
    )


async def _seed_message(conn, *, message_id, channel_id, content="stale"):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (1, "someone"),
    )
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (message_id, channel_id, 1, content),
    )


async def test_reconcile_converges_real_drift_against_postgres(db_conn, test_database_url):
    """The acceptance-criterion scenario: while the worker was down, one
    message was deleted on Discord (a missed MESSAGE_DELETE) and one new
    message was posted (a missed MESSAGE_CREATE). A single reconcile pass
    against real Postgres repairs both.
    """
    await _seed_guild_and_channel(db_conn)
    # Locally present, deleted on Discord during the outage.
    await _seed_message(db_conn, message_id=101, channel_id=10)
    # Locally present and still on Discord — should survive untouched.
    await _seed_message(db_conn, message_id=102, channel_id=10)
    await db_conn.commit()  # reconcile_channel uses its own pool connection, not db_conn

    author = FakeAuthor(id=1)
    fetcher = FakeFetcher(
        {
            100: [
                FakeMessage(id=102, author=author),  # still there
                FakeMessage(id=200, author=author),  # posted during the outage
            ]
        }
    )

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        async with pool.connection() as conn:
            sink = RepositoryReconciliationSink(conn)
            result = await reconcile_channel(
                fetcher, sink, channel_id=10, after=100, batch_size=100
            )
    finally:
        await pool.close()

    assert result.upserted == 2
    assert result.deleted == 1

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM messages WHERE channel_id = 10 ORDER BY id")
        remaining = {row["id"] for row in await cur.fetchall()}
    assert remaining == {102, 200}

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT last_reconciled_at FROM sync_state WHERE channel_id = 10")
        row = await cur.fetchone()
    assert row is not None
    assert row["last_reconciled_at"] is not None

    await _cleanup(db_conn)


async def test_reconcile_repairs_a_missed_edit(db_conn, test_database_url):
    await _seed_guild_and_channel(db_conn)
    await _seed_message(db_conn, message_id=101, channel_id=10, content="original")
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = FakeFetcher({100: [FakeMessage(id=101, author=author, content="edited")]})

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        async with pool.connection() as conn:
            sink = RepositoryReconciliationSink(conn)
            await reconcile_channel(fetcher, sink, channel_id=10, after=100, batch_size=100)
    finally:
        await pool.close()

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT content FROM messages WHERE id = 101")
        assert (await cur.fetchone())["content"] == "edited"

    await _cleanup(db_conn)
