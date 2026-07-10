"""PerRequestConnectionSource specifically -- not covered by the FakePool
used elsewhere in tests/integration/web/, which shares one already-open
connection across a whole test and so never actually exercises whether a
write gets committed. Uses sync psycopg (see conftest.py's web_conn) for
the same reason tests/e2e does: asyncio.run() from a fixture collides with
pytest-asyncio's session-wide event loop.
"""

import asyncio

import psycopg

from threadbare.web.db import PerRequestConnectionSource


def test_writes_are_committed_and_visible_to_a_separate_connection(test_database_url):
    async def _write():
        source = PerRequestConnectionSource(test_database_url)
        async with source.connection() as conn:
            await conn.execute(
                "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (999999999, "commit test guild"),
            )

    asyncio.run(_write())

    verify_conn = psycopg.connect(test_database_url)
    try:
        with verify_conn.cursor() as cur:
            cur.execute("SELECT name FROM guilds WHERE id = %s", (999999999,))
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "commit test guild"
    finally:
        with verify_conn.cursor() as cur:
            cur.execute("DELETE FROM guilds WHERE id = %s", (999999999,))
        verify_conn.commit()
        verify_conn.close()
