import os
import subprocess
import sys

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL is not set; see DEVELOPMENT.md", allow_module_level=True)

# Ensure the schema exists before any integration test runs, via a separate
# process rather than `asyncio.run()` in-process: an in-process async
# bootstrap at collection time leaves psycopg background tasks bound to a
# loop that's gone by the time pytest-asyncio creates its own per-test loops,
# producing "Cannot run the event loop while another loop is running" errors
# during later test teardown. A subprocess has no such shared state.
# Idempotent — safe to rerun every collection.
subprocess.run(
    [sys.executable, "-m", "threadbare.db.migrate"],
    env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
    check=True,
)


@pytest.fixture
def test_database_url() -> str:
    return TEST_DATABASE_URL


@pytest.fixture
async def db_conn():
    """An open connection to threadbare_test, rolled back after each test.

    Repository/orchestration functions must accept an already-open connection
    and never call commit()/rollback() themselves, so tests get per-test
    isolation for free without truncating tables between runs.
    """
    conn = await psycopg.AsyncConnection.connect(TEST_DATABASE_URL, autocommit=False)
    try:
        yield conn
    finally:
        await conn.rollback()
        await conn.close()
