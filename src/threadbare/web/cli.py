import asyncio
import os
import sys
import threading

from gunicorn.app.base import BaseApplication
from werkzeug.serving import run_simple

import threadbare
from threadbare import config
from threadbare.db.migrate import MigrationError, check_schema_up_to_date
from threadbare.logging_config import configure_logging
from threadbare.web.app import create_app
from threadbare.web.db import PerRequestConnectionSource
from threadbare.web.wizard_app import create_wizard_app

DEFAULT_PORT = 5000
# 127.0.0.1 is the safe default for a bare `uv run threadbare-web` on a dev
# machine; the Docker Compose stack sets HOST=0.0.0.0 for the web service
# so Caddy (a separate container) can reach it over the compose network.
DEFAULT_HOST = "127.0.0.1"
# A small VPS (DESIGN.md §8.4's Option B target, 2GB RAM) is comfortable
# with a handful of gunicorn workers; WEB_CONCURRENCY overrides this for
# bigger boxes.
DEFAULT_WORKERS = 4
# Long enough for the wizard's "All set" response to actually reach the
# browser before this process exits -- see on_complete below.
RESTART_DELAY_SECONDS = 1.0


class GunicornApplication(BaseApplication):
    """Loads an already-constructed WSGI app object directly, skipping
    gunicorn's usual `module:app` import-string convention -- lets main()
    hand it the same create_app()/create_wizard_app() instance every other
    entry point in this codebase already builds, rather than having
    gunicorn re-import and re-construct it itself.
    """

    def __init__(self, app, options=None):
        self.application = app
        self.options = options or {}
        super().__init__()

    def load_config(self):
        for key, value in self.options.items():
            self.cfg.set(key, value)

    def load(self):
        return self.application


def _run_gunicorn(app, host: str, port: int, workers: int) -> None:
    GunicornApplication(app, {"bind": f"{host}:{port}", "workers": workers}).run()


def main() -> None:
    if "--version" in sys.argv[1:]:
        print(f"threadbare {threadbare.__version__}")
        raise SystemExit(0)

    configure_logging()

    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", DEFAULT_PORT))

    if config.is_configured():
        settings = config.load_settings()
        try:
            asyncio.run(check_schema_up_to_date(settings.database_url))
        except MigrationError as e:
            print(e, file=sys.stderr)
            raise SystemExit(1) from e
        # Not db.pool.create_pool()'s AsyncConnectionPool -- it doesn't survive
        # Flask's async_to_sync bridge (see web/db.py's docstring).
        pool = PerRequestConnectionSource(settings.database_url)
        app = create_app(settings, pool)
        workers = int(os.environ.get("WEB_CONCURRENCY", DEFAULT_WORKERS))
        _run_gunicorn(app, host, port, workers)
        return

    # Unconfigured install: serve the first-run setup wizard instead of the
    # forum (ROADMAP.md §7, DESIGN.md §8). DATABASE_URL is assumed already
    # present (container-network Postgres, not something a mod hand-enters)
    # so the wizard can persist its own progress in Postgres even before
    # any Discord config exists -- but still surfaced as a clean error
    # rather than a raw traceback if it's genuinely missing too.
    try:
        database_url = config.get_database_url()
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e

    try:
        asyncio.run(check_schema_up_to_date(database_url))
    except MigrationError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e

    pool = PerRequestConnectionSource(database_url)

    def on_complete(new_settings: config.Settings) -> None:
        # gunicorn's multi-worker model forks separate OS processes, so the
        # in-process AppSwitcher hot-swap this used to do can't reach them
        # -- there's no single running app object to mutate anymore. Instead,
        # exit shortly after this returns (giving the "All set" response time
        # to reach the browser) and let Docker Compose's `restart:
        # unless-stopped` policy on the web service bring the container back
        # up; main() re-checks is_configured(), now true, and takes the
        # gunicorn branch above. Bare local `uv run threadbare-web` has no
        # such restart policy, so a developer finishing the wizard there has
        # to rerun the command themselves -- an accepted tradeoff for a
        # one-time setup flow, not an oversight.
        threading.Timer(RESTART_DELAY_SECONDS, os._exit, args=(0,)).start()

    wizard_app = create_wizard_app(pool, on_complete=on_complete)
    run_simple(host, port, wizard_app)


if __name__ == "__main__":
    main()
