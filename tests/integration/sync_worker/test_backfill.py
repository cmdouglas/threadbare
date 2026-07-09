from dataclasses import dataclass, field
from datetime import UTC, datetime

from threadbare.sync_worker.backfill import RepositoryBackfillSink, backfill_channel


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
        self.calls: list[int | None] = []

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls.append(after)
        return self._pages.get(after, [])


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, 0, 'general', true)
        """,
        (channel_id, guild_id),
    )


async def test_backfill_channel_writes_messages_to_real_db(db_conn):
    await _seed_guild_and_channel(db_conn)
    author = FakeAuthor(id=1)
    fetcher = FakeFetcher(
        {None: [FakeMessage(id=100, author=author), FakeMessage(id=101, author=author)]}
    )
    sink = RepositoryBackfillSink(db_conn)

    written = await backfill_channel(fetcher, sink, channel_id=10, batch_size=100)

    assert written == 2
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 2


async def test_backfill_channel_is_idempotent_on_rerun(db_conn):
    await _seed_guild_and_channel(db_conn)
    author = FakeAuthor(id=1)
    fetcher = FakeFetcher({None: [FakeMessage(id=100, author=author)]})
    sink = RepositoryBackfillSink(db_conn)

    await backfill_channel(fetcher, sink, channel_id=10, batch_size=100)
    await backfill_channel(fetcher, sink, channel_id=10, batch_size=100)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 1


async def test_backfill_resumes_from_persisted_checkpoint_after_restart(db_conn):
    await _seed_guild_and_channel(db_conn)
    author = FakeAuthor(id=1)
    sink = RepositoryBackfillSink(db_conn)

    # "First run": only page 1 is available (simulating a crash before the
    # channel's history was exhausted).
    first_fetcher = FakeFetcher(
        {None: [FakeMessage(id=100, author=author), FakeMessage(id=101, author=author)]}
    )
    await backfill_channel(first_fetcher, sink, channel_id=10, batch_size=2)

    # "Restart": a fresh sink/fetcher pair, but the checkpoint persisted to
    # Postgres by the first run carries over.
    second_fetcher = FakeFetcher({101: [FakeMessage(id=102, author=author)]})
    second_sink = RepositoryBackfillSink(db_conn)
    written = await backfill_channel(second_fetcher, second_sink, channel_id=10, batch_size=2)

    assert written == 1
    assert second_fetcher.calls[0] == 101
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 3
