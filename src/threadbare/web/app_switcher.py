"""Mutable WSGI dispatcher -- lets web/cli.py's process drop out of setup-
wizard mode into the real forum app in place, once .env is written and
Settings reloads cleanly, with no restart. Not werkzeug's
DispatcherMiddleware (that routes by path prefix to different sub-apps);
this replaces the whole app in one call. The sync worker has no equivalent
-- there's no IPC to it, and it isn't hot-reloaded by design (the wizard's
finish step tells the operator to restart it themselves).
"""

from collections.abc import Callable
from typing import Any


class AppSwitcher:
    def __init__(self, initial_app: Callable) -> None:
        self._current = initial_app

    def switch_to(self, app: Callable) -> None:
        self._current = app

    @property
    def current(self) -> Callable:
        """Read access to whichever app is currently active -- e.g. for a
        test that wants to drive the underlying Flask app's own test_client()
        rather than issuing real HTTP requests against this WSGI callable.
        """
        return self._current

    def __call__(self, environ: dict, start_response: Callable) -> Any:
        return self._current(environ, start_response)
