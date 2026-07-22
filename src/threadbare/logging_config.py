"""Shared logging setup for all three CLI entry points (web, sync worker,
migrate) -- stdlib-only, no file-based logging: stdout/stderr is Docker's
own log driver's job, not this app's (see docs/self-hosting.md's
`docker compose logs -f <service>`).
"""

import logging
import os

DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format=DEFAULT_LOG_FORMAT)
