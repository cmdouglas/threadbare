"""Real, browser-driven Playwright tests against a live werkzeug server --
the first real e2e tests this project has, now that §4 produces actual
pages to click through (everything before it was backend-only). Runs
entirely separately from tests/unit and tests/integration: pytest-playwright's
sync driver and pytest-asyncio's runner corrupt each other's event-loop
state when collected into the same session, so this tier is deliberately
excluded from pyproject.toml's default testpaths (see DEVELOPMENT.md).

live_server runs the real Flask app (async views, PerRequestConnectionSource
-- the same production wiring as threadbare-web) via werkzeug's dev server
in a background thread, driven by real HTTP requests from a real browser.
Data is seeded via committed writes, not the rollback-based db_conn fixture
used elsewhere: a background thread's connections are independent of
whatever connection a test uses to seed, so only committed data is visible
to it. Seeding uses plain *synchronous* psycopg (psycopg.connect, not
AsyncConnection) deliberately: pytest-asyncio's asyncio_mode="auto" keeps an
event loop alive for the whole session even with no async test functions in
this tier, and calling asyncio.run() from a fixture here collides with it
("cannot be called from a running event loop") -- confirmed directly, not
a guess. live_server itself needs no such workaround: Flask's async views
only run inside werkzeug's background thread, driven by real HTTP requests
from the browser, already proven independent of pytest's own event loop.
"""

import os
import subprocess
import sys
import threading
from dataclasses import dataclass

import psycopg
import pytest
from dotenv import load_dotenv
from flask import Flask
from psycopg.rows import dict_row
from werkzeug.serving import make_server

from threadbare.config import Settings
from threadbare.web.app import create_app
from threadbare.web.db import PerRequestConnectionSource

load_dotenv()

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL is not set; see DEVELOPMENT.md", allow_module_level=True)

# Matches tests/integration/conftest.py's precedent: a subprocess rather
# than an in-process asyncio bootstrap, since bootstrapping here leaves
# background tasks bound to a loop that's gone by the time real test
# execution starts.
subprocess.run(
    [sys.executable, "-m", "threadbare.db.migrate"],
    env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
    check=True,
)

E2E_GUILD_ID = 1


@dataclass
class LiveServer:
    """Wraps the base URL (the only thing most e2e tests need) alongside
    the real Flask `app` object, needed only by tests that must construct a
    signed session cookie directly -- Playwright can't monkeypatch code
    running in live_server's background thread, so seeding a valid session
    is how auth-gated e2e tests get past the login gate without a real
    Discord OAuth round trip (see test_admin_and_login_gate.py).

    __str__ returns base_url so every pre-existing `f"{live_server}/path"`
    call site keeps working unchanged.
    """

    base_url: str
    app: Flask

    def __str__(self) -> str:
        return self.base_url

    def session_cookie(self, **session_data) -> dict:
        serializer = self.app.session_interface.get_signing_serializer(self.app)
        return {
            "name": self.app.config["SESSION_COOKIE_NAME"],
            "value": serializer.dumps(session_data),
            "url": self.base_url,
        }


@pytest.fixture(scope="session")
def live_server():
    settings = Settings(
        discord_bot_token="e2e-test-token",
        discord_guild_id=E2E_GUILD_ID,
        database_url=TEST_DATABASE_URL,
        discord_client_id="e2e-client-id",
        discord_client_secret="e2e-client-secret",
        discord_oauth_redirect_uri="http://127.0.0.1/oauth/callback",
        flask_secret_key="e2e-test-secret-key",
    )
    pool = PerRequestConnectionSource(TEST_DATABASE_URL)
    app = create_app(settings, pool)
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield LiveServer(base_url=f"http://127.0.0.1:{server.server_port}", app=app)
    server.shutdown()
    thread.join()


@pytest.fixture
def seed_conn():
    """A real, committing, synchronous connection -- unlike db_conn, writes
    here are visible to live_server's own separate (async) connections.
    """
    conn = psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row)
    yield conn
    conn.close()


@pytest.fixture
def context(context, live_server):
    """Overrides pytest-playwright's own `context` fixture (the standard
    way to extend it -- its docs use this same pattern for auth) so every
    test gets a logged-in session by default now that the login gate
    (web/app.py's require_login) covers every route. Auth/admin-gate tests
    that need to exercise anonymous or mod-elevated state use
    `anonymous_page`/override the cookie themselves instead.
    """
    context.add_cookies(
        [live_server.session_cookie(user_id=1, display_name="e2e-user", is_mod=False)]
    )
    return context


@pytest.fixture
def anonymous_page(browser):
    """A page with no session cookie at all -- for exercising the login
    gate itself, bypassing the auto-login `context` override above.
    """
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()
