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
from threadbare.web.wizard_app import create_wizard_app


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
        discord_bot_token="test-bot-token",
        discord_guild_id=1,
        database_url="unused",
        discord_client_id="test-client-id",
        discord_client_secret="test-client-secret",
        discord_oauth_redirect_uri="http://localhost:5000/oauth/callback",
        flask_secret_key="test-secret-key",
    )
    return create_app(settings, FakePool(web_conn))


@pytest.fixture
def anonymous_client(app):
    """A test client with no session at all -- for exercising the login
    gate itself and the login/callback flow. Most tests want `client`
    instead, which is pre-authenticated as an ordinary (non-mod) member,
    since the login gate now applies to every route (web/app.py's
    `require_login` before_request hook).
    """
    return app.test_client()


@pytest.fixture
def client(anonymous_client):
    with anonymous_client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["display_name"] = "test-user"
        sess["is_mod"] = False
    return anonymous_client


def run(coro):
    """Runs a coroutine to completion from a sync test function -- the
    seeding/assertion counterpart to web_conn being usable from sync code.
    """
    return asyncio.run(coro)


@pytest.fixture
def wizard_app(web_conn, tmp_path):
    return create_wizard_app(FakePool(web_conn), env_file_path=tmp_path / ".env")


@pytest.fixture
def wizard_client(wizard_app):
    return wizard_app.test_client()


def stub_oauth_functions(monkeypatch, module, *, user, guilds):
    """Shared by test_auth.py (the real login gate) and
    test_wizard_oauth_step.py (the wizard's own test-login round trip) --
    both call the same three threadbare.web.discord_rest OAuth functions,
    just from different view modules.
    """

    async def fake_exchange(**kwargs):
        return {"access_token": "tok123"}

    async def fake_get_user(token, **kwargs):
        return user

    async def fake_get_guilds(token, **kwargs):
        return guilds

    monkeypatch.setattr(module, "exchange_oauth_code", fake_exchange)
    monkeypatch.setattr(module, "get_current_user", fake_get_user)
    monkeypatch.setattr(module, "get_current_user_guilds", fake_get_guilds)
