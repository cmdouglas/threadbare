"""Flask's async views run through flask[async]/asgiref's async_to_sync
bridge, which conflicts with pytest-asyncio's own event loop the same way
already documented for Playwright in pyproject.toml -- confirmed directly:
an `async def` test function calling Flask's (sync) test client against an
async view raises "You cannot use AsyncToSync in the same thread as an
async event loop." So unlike every other integration test package, this
one uses plain sync test functions, with a sync `web_conn` fixture that
wraps asyncio.run() around connection setup/teardown itself rather than
relying on pytest-asyncio's async-fixture machinery. See web/db.py's
docstring for the companion finding about connection pooling across this
same bridge.
"""

import asyncio
from contextlib import asynccontextmanager

import psycopg
import pytest
from psycopg.rows import dict_row

from threadbare.config import Settings
from threadbare.web.app import create_app


class FakePool:
    """Always yields the same connection -- test isolation comes from
    web_conn's rollback, not from real pooling.
    """

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self._conn


@pytest.fixture
def web_conn(test_database_url):
    conn = asyncio.run(
        psycopg.AsyncConnection.connect(test_database_url, autocommit=False, row_factory=dict_row)
    )
    yield conn
    asyncio.run(conn.rollback())
    asyncio.run(conn.close())


@pytest.fixture
def app(web_conn):
    settings = Settings(
        discord_bot_token="test-bot-token", discord_guild_id=1, database_url="unused"
    )
    return create_app(settings, FakePool(web_conn))


@pytest.fixture
def client(app):
    return app.test_client()


def run(coro):
    """Runs a coroutine to completion from a sync test function -- the
    seeding/assertion counterpart to web_conn being usable from sync code.
    """
    return asyncio.run(coro)
