"""Validates the other half of ROADMAP.md's million-message v1 acceptance
criterion: "...then browses at server-side page-load times under 200ms."
Seeds a genuinely 1,000,000-row channel (via synthetic.bulk_seed_channel's
set-based bulk insert -- see that function's docstring for why this doesn't
reuse the real backfill pipeline) once for the whole module, then measures
real Flask-test-client request time (view + template render, the actual
"page load" a browser would wait on) against it.

Opt-in (pytest.mark.performance), sync test functions (not `async def`) --
same Flask async_to_sync/pytest-asyncio event-loop conflict documented in
tests/integration/web/conftest.py, which this package's own conftest.py's
web_conn/app/client fixtures mirror.
"""

import asyncio
import time

import pytest

from threadbare.db.pool import create_pool

from . import synthetic
from .conftest import PERFORMANCE_GUILD_ID, TEST_DATABASE_URL

pytestmark = pytest.mark.performance

CHANNEL_ID = PERFORMANCE_GUILD_ID
TOTAL_MESSAGES = 1_000_000
# A disjoint message-id space from test_million_message_backfill.py's
# 1..TOTAL_MESSAGES range -- see bulk_seed_channel's docstring for the real
# incident (an id collision with that other test's leftover rows silently
# overwrote the wrong channel_id) this defends against.
ID_OFFSET = 5_000_000_000
PAGE_SIZE = 25
LAST_PAGE = TOTAL_MESSAGES // PAGE_SIZE
TIME_LIMIT_SECONDS = 0.2


@pytest.fixture(scope="module")
def seeded_channel():
    """Plain sync fixture wrapping asyncio.run() itself (like this
    package's own web_conn fixture) rather than an async fixture -- sidesteps
    pytest-asyncio's per-test event-loop scoping entirely, which a
    module-scoped *async* fixture would otherwise fight with.
    """

    async def _seed():
        pool = create_pool(TEST_DATABASE_URL)
        await pool.open()
        try:
            async with pool.connection() as conn:
                await synthetic.bulk_seed_channel(
                    conn,
                    guild_id=PERFORMANCE_GUILD_ID,
                    channel_id=CHANNEL_ID,
                    total_messages=TOTAL_MESSAGES,
                    id_offset=ID_OFFSET,
                )
                await conn.commit()
                # VACUUM can't run inside a transaction block, hence the
                # commit above and the autocommit toggle here. A freshly
                # bulk-inserted table has no visibility-map bits set, which
                # degrades index-only scans (count_messages_before) into a
                # full heap fetch per row -- a real, measured ~360ms cost
                # this fixture would otherwise be the only place introducing
                # it, since a real Discord channel accumulates messages
                # gradually with autovacuum keeping the visibility map
                # current the whole time. Vacuuming after the bulk seed
                # makes this fixture's starting state representative of that
                # steady state instead of an artificially-worse one.
                await conn.set_autocommit(True)
                await conn.execute("VACUUM ANALYZE messages")
                await conn.set_autocommit(False)
        finally:
            await pool.close()

    async def _cleanup():
        pool = create_pool(TEST_DATABASE_URL)
        await pool.open()
        try:
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM messages WHERE channel_id = %s", (CHANNEL_ID,))
                await conn.execute("DELETE FROM channels WHERE id = %s", (CHANNEL_ID,))
                await conn.execute("DELETE FROM guilds WHERE id = %s", (PERFORMANCE_GUILD_ID,))
                await conn.execute("DELETE FROM users")
                await conn.commit()
        finally:
            await pool.close()

    start = time.monotonic()
    asyncio.run(_seed())
    print(f"\nBulk-seeded {TOTAL_MESSAGES:,} messages in {time.monotonic() - start:.1f}s")
    yield
    asyncio.run(_cleanup())


def _timed_get(client, path: str):
    start = time.perf_counter()
    response = client.get(path)
    elapsed = time.perf_counter() - start
    print(f"{path} -> {response.status_code} in {elapsed * 1000:.1f}ms")
    return response, elapsed


def test_board_index_loads_under_200ms(client, seeded_channel):
    response, elapsed = _timed_get(client, "/")
    assert response.status_code == 200
    assert elapsed < TIME_LIMIT_SECONDS


def test_first_page_of_a_million_message_board_loads_under_200ms(client, seeded_channel):
    response, elapsed = _timed_get(client, f"/board/{CHANNEL_ID}/continuous/page/1")
    assert response.status_code == 200
    assert elapsed < TIME_LIMIT_SECONDS


def test_last_page_of_a_million_message_board_loads_under_200ms(client, seeded_channel):
    # The board's read path (db/queries.get_messages_page) used to paginate
    # purely via forward OFFSET -- Postgres genuinely walked all ~999,975
    # preceding index entries to serve the last page of a 1,000,000-row
    # board (~700-964ms measured) even though only 25 rows come back. Fixed
    # by fetching from whichever end of the (posted_at, id) index is closer
    # (get_messages_page's `total` kwarg); this specifically stresses that
    # fix's exact target case (the very last page) rather than only ever
    # measuring the cheap first page. Real, measured result now ~65-70ms --
    # see RESOLVED_ISSUES.md.
    response, elapsed = _timed_get(client, f"/board/{CHANNEL_ID}/continuous/page/{LAST_PAGE}")
    assert response.status_code == 200
    assert elapsed < TIME_LIMIT_SECONDS


def test_search_loads_under_200ms(client, seeded_channel):
    response, elapsed = _timed_get(client, f"/search?q={synthetic.SEARCH_NEEDLE}")
    assert response.status_code == 200
    assert elapsed < TIME_LIMIT_SECONDS
