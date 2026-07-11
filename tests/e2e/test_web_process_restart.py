"""Proves the second half of the wizard's restart-on-finish hand-off (the
first half -- the wizard writing .env and invoking on_complete -- is proven
in test_wizard_first_run.py, in-process): a real `threadbare-web` process,
started fresh against an already-configured environment, serves the real
forum app under gunicorn rather than Werkzeug's dev server.

This is a real subprocess (not an in-thread werkzeug server like every other
e2e fixture in this project) specifically because gunicorn forks separate
OS worker processes -- proving it actually runs means actually running it,
not faking a WSGI callable in-process. It uses plain HTTP (httpx) rather
than Playwright: there's no page to click through here, just a
process-lifecycle and one redirect header to confirm.

What this deliberately does NOT prove: that Docker Compose's `restart:
unless-stopped` policy actually brings the container back up after
web/cli.py's on_complete calls os._exit(0) -- that's Compose's own
supervision behavior, not this codebase's, and re-implementing Docker's
restart policy in the test harness isn't worth it. That step is verified
manually against a real `docker compose up` run instead (see ROADMAP.md §8).
"""

import os
import signal
import socket
import subprocess
import sys
import time

import httpx

# conftest.py already skips this whole tier at collection time if
# TEST_DATABASE_URL isn't set, so it's guaranteed non-None by the time any
# test in this module actually runs.
from tests.e2e.conftest import TEST_DATABASE_URL


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_serving(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://{host}:{port}/", timeout=1.0, follow_redirects=False)
            return
        except httpx.HTTPError as e:
            last_error = e
            time.sleep(0.1)
    raise TimeoutError(
        f"nothing answered http://{host}:{port}/ within {timeout}s"
    ) from last_error


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=5)


def test_configured_process_serves_the_real_forum_app_under_gunicorn():
    host = "127.0.0.1"
    port = _free_port()
    env = {
        **os.environ,
        "HOST": host,
        "PORT": str(port),
        "WEB_CONCURRENCY": "2",
        "DATABASE_URL": TEST_DATABASE_URL,
        "DISCORD_BOT_TOKEN": "e2e-restart-test-token",
        "DISCORD_CLIENT_ID": "e2e-restart-client-id",
        "DISCORD_CLIENT_SECRET": "e2e-restart-client-secret",
        "DISCORD_OAUTH_REDIRECT_URI": f"http://{host}:{port}/oauth/callback",
        "DISCORD_TEST_GUILD_ID": "1",
        "FLASK_SECRET_KEY": "e2e-restart-test-secret-key",
    }

    process = subprocess.Popen(
        [sys.executable, "-m", "threadbare.web.cli"],
        env=env,
        start_new_session=True,  # own process group, so gunicorn's forked
        # workers die with it in _terminate() rather than being orphaned
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_until_serving(host, port)

        # "/" hits the real forum app's login gate (require_login ->
        # redirect to /login -> redirect to Discord's real authorize URL),
        # which doesn't exist in the wizard app at all -- proof this process
        # is serving create_app()'s real app, not the wizard, and it's doing
        # so as a genuinely separate OS process (this test never touched an
        # in-process app object). Chased by hand (not follow_redirects=True)
        # so the test never actually issues the final request to
        # discord.com itself.
        root_resp = httpx.get(f"http://{host}:{port}/", follow_redirects=False)
        assert root_resp.status_code == 302
        assert root_resp.headers["location"] == "/login"

        login_resp = httpx.get(
            f"http://{host}:{port}/login", follow_redirects=False
        )
        assert login_resp.status_code == 302
        assert login_resp.headers["location"].startswith(
            "https://discord.com/oauth2/authorize"
        )
    finally:
        _terminate(process)
