"""Own copy of tests/integration/conftest.py's TEST_DATABASE_URL/db_conn
fixtures rather than a cross-directory import — pytest's conftest.py
discovery only applies to a directory and its own subtree, not siblings, and
this project's existing convention (see test_backfill.py's docstring) is to
duplicate a small fixture rather than build sharing machinery for it.
"""

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg.rows import dict_row

from threadbare.config import Settings
from threadbare.web.app import create_app

load_dotenv()

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

# Shared between the `app` fixture's Settings and any test that seeds a
# guild/channel to browse through the Flask test client -- must agree, since
# board_index/board/search views all scope reads to settings.discord_guild_id.
PERFORMANCE_GUILD_ID = 9002

if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL is not set; see DEVELOPMENT.md", allow_module_level=True)

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
    conn = await psycopg.AsyncConnection.connect(
        TEST_DATABASE_URL, autocommit=False, row_factory=dict_row
    )
    try:
        yield conn
    finally:
        await conn.rollback()
        await conn.close()


class FakePool:
    """Always yields the same connection, matching
    tests/integration/web/conftest.py's fixture of the same name -- test
    isolation comes from web_conn's rollback, not from real pooling.
    """

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


@pytest.fixture
def web_conn():
    """Sync-wrapped counterpart to db_conn, for tests that drive a Flask
    test client. Same reason tests/integration/web/conftest.py needs one:
    Flask's async_to_sync bridge conflicts with pytest-asyncio's own event
    loop, so a test exercising the app must be a plain sync test function,
    not `async def`.
    """
    conn = asyncio.run(
        psycopg.AsyncConnection.connect(TEST_DATABASE_URL, autocommit=False, row_factory=dict_row)
    )
    yield conn
    asyncio.run(conn.rollback())
    asyncio.run(conn.close())


@pytest.fixture
def app(web_conn):
    settings = Settings(
        discord_bot_token="test-bot-token",
        discord_guild_id=PERFORMANCE_GUILD_ID,
        database_url="unused",
        discord_client_id="test-client-id",
        discord_client_secret="test-client-secret",
        discord_oauth_redirect_uri="http://localhost:5000/oauth/callback",
        flask_secret_key="test-secret-key",
    )
    return create_app(settings, FakePool(web_conn))


@pytest.fixture
def client(app):
    test_client = app.test_client()
    with test_client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["display_name"] = "test-user"
        sess["is_mod"] = False
    return test_client


def run(coro):
    """Runs a coroutine to completion from a sync test function."""
    return asyncio.run(coro)
