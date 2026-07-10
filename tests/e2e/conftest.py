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

import psycopg
import pytest
from dotenv import load_dotenv
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


@pytest.fixture(scope="session")
def live_server():
    settings = Settings(
        discord_bot_token="e2e-test-token",
        discord_guild_id=E2E_GUILD_ID,
        database_url=TEST_DATABASE_URL,
    )
    pool = PerRequestConnectionSource(TEST_DATABASE_URL)
    app = create_app(settings, pool)
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
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
