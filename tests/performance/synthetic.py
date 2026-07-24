"""Synthetic Discord-shaped data generation shared by the performance tier.

Structurally mirrors tests/integration/sync_worker/test_backfill.py's
FakeAuthor/FakeMessage (same fields, same defaults) rather than reusing them
directly -- that module isn't a shared library, just another test file, and
duplicating a five-field dataclass is cheaper than inventing cross-directory
test-helper sharing for it (same reasoning as this package's own conftest.py).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

BASE_TIME = datetime(2020, 1, 1, tzinfo=UTC)
DISTINCT_AUTHORS = 500
# Sprinkled into ~0.1% of messages so full-text search has a real, small
# result set to page through rather than either zero matches or the entire
# table -- exercises search_messages/count_search_results at a realistic
# selectivity, not a worst-case (every row matches) or vacuous (no rows
# match) case.
SEARCH_NEEDLE = "throckmorton"
SEARCH_NEEDLE_INTERVAL = 1000


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


def synthetic_message(message_id: int) -> FakeMessage:
    author_id = (message_id % DISTINCT_AUTHORS) + 1
    author = FakeAuthor(id=author_id, display_name=f"user-{author_id}")
    content = f"Synthetic message number {message_id} for load testing."
    if message_id % SEARCH_NEEDLE_INTERVAL == 0:
        content += f" {SEARCH_NEEDLE}"
    return FakeMessage(
        id=message_id,
        author=author,
        content=content,
        # Strictly increasing with id, one second apart -- messages_channel_
        # id_posted_at_idx is (channel_id, posted_at, id) and every read path
        # sorts on (posted_at, id), so this keeps posted_at order consistent
        # with id order the way a real Discord channel's history always is.
        created_at=BASE_TIME + timedelta(seconds=message_id),
    )


class SyntheticHistoryFetcher:
    """A HistoryFetcher (see sync_worker/backfill.py's Protocol) generating
    `total_messages` deterministic messages on the fly -- no live Discord
    connection, no pre-materialized list held in memory at once.
    """

    def __init__(self, total_messages: int):
        self.total_messages = total_messages

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        start = (after or 0) + 1
        if start > self.total_messages:
            return []
        end = min(start + limit - 1, self.total_messages)
        return [synthetic_message(message_id) for message_id in range(start, end + 1)]


async def bulk_seed_channel(
    conn, *, guild_id: int, channel_id: int, total_messages: int, id_offset: int = 0
) -> None:
    """Seeds a public channel with `total_messages` rows directly via one
    set-based INSERT ... SELECT generate_series(...), not a per-row Python
    loop through the real backfill pipeline -- deliberately: this is for the
    read-path (page-load timing) performance tests, which need a genuinely
    1,000,000-row table to measure Postgres/query performance against, not
    another exercise of the ingestion pipeline (that's
    test_million_message_backfill.py's job, which does drive the real
    pipeline row by row specifically because its own logic is what's under
    test there). Content/authorship follows the same scheme as
    synthetic_message() above (distinct author pool, an occasional
    SEARCH_NEEDLE) so search/pagination see realistic-ish data, though the
    two code paths don't need to produce byte-identical output -- they seed
    entirely separate channels in separate test files.

    id_offset exists because of a real incident during this suite's own
    development: messages.id is a single global primary key (real Discord
    snowflakes are globally unique too, never scoped per channel), and this
    function's ids and test_million_message_backfill.py's synthetic ids both
    default to the same 1..total_messages range. When that other test's
    channel got left mid-cleanup (an interrupted teardown, a manual kill --
    see RESOLVED_ISSUES.md), its orphaned rows collided id-for-id with a
    later run of *this* seed, and upsert_message()'s ON CONFLICT clause
    deliberately never touches channel_id/thread_id/author_id (matching
    upsert_channel's precedent -- a message's container is immutable once
    created, same as Discord's own model), so every "insert" silently
    overwrote the other test's rows in place instead of creating new ones --
    a real message count of zero under this function's own channel_id,
    with no exception anywhere to signal it. Giving each performance test
    file a disjoint id space makes that whole failure mode structurally
    impossible, independent of whether cleanup ever behaves.
    """
    await conn.execute(
        """
        INSERT INTO users (id, display_name)
        SELECT gs, 'user-' || gs
        FROM generate_series(1, %(distinct_authors)s) AS gs
        ON CONFLICT (id) DO NOTHING
        """,
        {"distinct_authors": DISTINCT_AUTHORS},
    )
    await conn.execute(
        """
        INSERT INTO guilds (id, name) VALUES (%(guild_id)s, 'Perf Test Guild')
        ON CONFLICT (id) DO NOTHING
        """,
        {"guild_id": guild_id},
    )
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%(channel_id)s, %(guild_id)s, 0, 'million-message-board', true)
        """,
        {"channel_id": channel_id, "guild_id": guild_id},
    )
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, thread_id, author_id, content, posted_at)
        SELECT
            gs + %(id_offset)s,
            %(channel_id)s,
            NULL,
            mod(gs - 1, %(distinct_authors)s) + 1,
            'Synthetic message number ' || gs || ' for load testing.'
                || CASE WHEN mod(gs, %(search_interval)s) = 0 THEN ' ' || %(needle)s ELSE '' END,
            %(base_time)s + (gs::text || ' seconds')::interval
        FROM generate_series(1, %(total)s) AS gs
        """,
        {
            "channel_id": channel_id,
            "distinct_authors": DISTINCT_AUTHORS,
            "search_interval": SEARCH_NEEDLE_INTERVAL,
            "needle": SEARCH_NEEDLE,
            "base_time": BASE_TIME,
            "total": total_messages,
            "id_offset": id_offset,
        },
    )


class CrashAfterNMessagesFetcher:
    """Wraps a HistoryFetcher, raising once it's already served
    `crash_after` messages -- simulates a sync worker process dying mid-
    backfill (killed, OOM, network drop) after some already-committed
    progress, so a resumed run can be proven to pick up from the real
    persisted checkpoint rather than restarting from scratch.
    """

    def __init__(self, fetcher, *, crash_after: int):
        self._fetcher = fetcher
        self._crash_after = crash_after
        self._served = 0

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        if self._served >= self._crash_after:
            raise RuntimeError("simulated sync worker crash mid-backfill")
        batch = await self._fetcher.fetch_batch(channel_id=channel_id, after=after, limit=limit)
        self._served += len(batch)
        return batch
