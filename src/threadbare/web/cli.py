import sys

from threadbare.config import ConfigError, load_settings
from threadbare.web.app import create_app
from threadbare.web.db import PerRequestConnectionSource

DEFAULT_PORT = 5000


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e

    # Not db.pool.create_pool()'s AsyncConnectionPool -- it doesn't survive
    # Flask's async_to_sync bridge (see web/db.py's docstring).
    pool = PerRequestConnectionSource(settings.database_url)
    app = create_app(settings, pool)
    app.run(port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
