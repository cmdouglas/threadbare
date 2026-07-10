import sys

from werkzeug.serving import run_simple

from threadbare import config
from threadbare.web.app import create_app
from threadbare.web.app_switcher import AppSwitcher
from threadbare.web.db import PerRequestConnectionSource
from threadbare.web.wizard_app import create_wizard_app

DEFAULT_PORT = 5000


def main() -> None:
    if config.is_configured():
        settings = config.load_settings()
        # Not db.pool.create_pool()'s AsyncConnectionPool -- it doesn't survive
        # Flask's async_to_sync bridge (see web/db.py's docstring).
        pool = PerRequestConnectionSource(settings.database_url)
        app = create_app(settings, pool)
        app.run(port=DEFAULT_PORT)
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
    pool = PerRequestConnectionSource(database_url)

    def on_complete(new_settings: config.Settings) -> None:
        new_pool = PerRequestConnectionSource(new_settings.database_url)
        switcher.switch_to(create_app(new_settings, new_pool))

    wizard_app = create_wizard_app(pool, on_complete=on_complete)
    switcher = AppSwitcher(wizard_app)

    run_simple("127.0.0.1", DEFAULT_PORT, switcher)


if __name__ == "__main__":
    main()
