"""Proves web/cli.py's own main() -- not just create_wizard_app()/
create_app() in isolation -- actually branches correctly: an unconfigured
install serves the wizard (ROADMAP.md §7's "first-run detection"), not a
crash. Monkeypatches dotenv.load_dotenv to a no-op so this test's outcome
never depends on whether a real, fully-populated .env happens to exist on
the machine running the suite (config.load_settings()/is_configured()
otherwise call the real load_dotenv(), which would silently fill in
"missing" env vars from a real repo .env if one exists) -- and monkeypatches
run_simple/_run_gunicorn so this never binds a real socket (the hardcoded
production port, 5000, is unreliable to bind in tests: confirmed firsthand
elsewhere in this project that macOS AirPlay Receiver squats it by default).

The configured branch no longer calls app.run() directly -- gunicorn (a
real multi-worker WSGI server) replaced Werkzeug's dev server once the
wizard's AppSwitcher hot-swap (which only worked within a single process)
was retired in favor of a restart-on-finish handoff. See on_complete's test
below for that handoff itself.
"""

import psycopg
import pytest

from threadbare.web import cli


def test_main_serves_the_wizard_app_when_unconfigured(monkeypatch, test_database_url):
    # This test builds a real (non-rollback) PerRequestConnectionSource, same
    # as production -- so wizard_state must be reset first, or a row left
    # behind by an earlier e2e test (also real-committing) would make this
    # test's outcome depend on run order.
    conn = psycopg.connect(test_database_url)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE wizard_state")
    conn.commit()
    conn.close()

    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    for var in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "DISCORD_OAUTH_REDIRECT_URI",
        "DISCORD_TEST_GUILD_ID",
        "FLASK_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_URL", test_database_url)

    captured = {}

    def fake_run_simple(host, port, app, **kwargs):
        captured["app"] = app
        captured["host"] = host

    monkeypatch.setattr(cli, "run_simple", fake_run_simple)

    cli.main()

    client = captured["app"].test_client()
    resp = client.get("/intro")
    assert resp.status_code == 200
    assert b"Welcome to Threadbare" in resp.data
    assert captured["host"] == "127.0.0.1"


def test_main_wizard_mode_uses_host_env_var_when_set(monkeypatch, test_database_url):
    conn = psycopg.connect(test_database_url)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE wizard_state")
    conn.commit()
    conn.close()

    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    for var in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "DISCORD_OAUTH_REDIRECT_URI",
        "DISCORD_TEST_GUILD_ID",
        "FLASK_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("HOST", "0.0.0.0")

    captured = {}

    def fake_run_simple(host, port, app, **kwargs):
        captured["host"] = host

    monkeypatch.setattr(cli, "run_simple", fake_run_simple)

    cli.main()

    assert captured["host"] == "0.0.0.0"


def test_main_serves_the_real_forum_app_when_configured(monkeypatch, test_database_url):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CLIENT_ID", "cid")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DISCORD_OAUTH_REDIRECT_URI", "http://localhost:5000/oauth/callback")
    monkeypatch.setenv("DISCORD_TEST_GUILD_ID", "1")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-key")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)

    fake_app = object()
    captured = {}

    monkeypatch.setattr(cli, "create_app", lambda settings, pool: fake_app)
    monkeypatch.setattr(
        cli,
        "_run_gunicorn",
        lambda app, host, port, workers: captured.update(
            app=app, host=host, port=port, workers=workers
        ),
    )

    cli.main()

    assert captured["app"] is fake_app
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == cli.DEFAULT_PORT
    assert captured["workers"] == cli.DEFAULT_WORKERS


def test_main_configured_mode_uses_host_env_var_when_set(monkeypatch, test_database_url):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CLIENT_ID", "cid")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DISCORD_OAUTH_REDIRECT_URI", "http://localhost:5000/oauth/callback")
    monkeypatch.setenv("DISCORD_TEST_GUILD_ID", "1")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-key")
    monkeypatch.setenv("HOST", "0.0.0.0")

    captured = {}

    monkeypatch.setattr(cli, "create_app", lambda settings, pool: object())
    monkeypatch.setattr(
        cli, "_run_gunicorn", lambda app, host, port, workers: captured.update(host=host)
    )

    cli.main()

    assert captured["host"] == "0.0.0.0"


def test_main_configured_mode_uses_port_env_var_when_set(monkeypatch, test_database_url):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CLIENT_ID", "cid")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DISCORD_OAUTH_REDIRECT_URI", "http://localhost:5000/oauth/callback")
    monkeypatch.setenv("DISCORD_TEST_GUILD_ID", "1")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-key")
    monkeypatch.setenv("PORT", "8123")

    captured = {}

    monkeypatch.setattr(cli, "create_app", lambda settings, pool: object())
    monkeypatch.setattr(
        cli, "_run_gunicorn", lambda app, host, port, workers: captured.update(port=port)
    )

    cli.main()

    assert captured["port"] == 8123


def test_main_wizard_mode_uses_port_env_var_when_set(monkeypatch, test_database_url):
    conn = psycopg.connect(test_database_url)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE wizard_state")
    conn.commit()
    conn.close()

    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    for var in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "DISCORD_OAUTH_REDIRECT_URI",
        "DISCORD_TEST_GUILD_ID",
        "FLASK_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("PORT", "8124")

    captured = {}

    def fake_run_simple(host, port, app, **kwargs):
        captured["port"] = port

    monkeypatch.setattr(cli, "run_simple", fake_run_simple)

    cli.main()

    assert captured["port"] == 8124


def test_main_configured_mode_uses_web_concurrency_env_var_when_set(
    monkeypatch, test_database_url
):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CLIENT_ID", "cid")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DISCORD_OAUTH_REDIRECT_URI", "http://localhost:5000/oauth/callback")
    monkeypatch.setenv("DISCORD_TEST_GUILD_ID", "1")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-key")
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    captured = {}

    monkeypatch.setattr(cli, "create_app", lambda settings, pool: object())
    monkeypatch.setattr(
        cli, "_run_gunicorn", lambda app, host, port, workers: captured.update(workers=workers)
    )

    cli.main()

    assert captured["workers"] == 2


def test_main_configured_mode_exits_when_schema_check_fails(
    monkeypatch, test_database_url, capsys
):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CLIENT_ID", "cid")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DISCORD_OAUTH_REDIRECT_URI", "http://localhost:5000/oauth/callback")
    monkeypatch.setenv("DISCORD_TEST_GUILD_ID", "1")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-key")

    async def fake_check_schema_up_to_date(dsn):
        raise cli.MigrationError("1 pending migration(s) not yet applied: 9999_fake")

    monkeypatch.setattr(cli, "check_schema_up_to_date", fake_check_schema_up_to_date)
    monkeypatch.setattr(cli, "create_app", lambda settings, pool: object())
    monkeypatch.setattr(cli, "_run_gunicorn", lambda app, host, port, workers: None)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "9999_fake" in capsys.readouterr().err


def test_main_wizard_mode_exits_when_schema_check_fails(monkeypatch, test_database_url, capsys):
    conn = psycopg.connect(test_database_url)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE wizard_state")
    conn.commit()
    conn.close()

    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    for var in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "DISCORD_OAUTH_REDIRECT_URI",
        "DISCORD_TEST_GUILD_ID",
        "FLASK_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_URL", test_database_url)

    async def fake_check_schema_up_to_date(dsn):
        raise cli.MigrationError("1 pending migration(s) not yet applied: 9999_fake")

    monkeypatch.setattr(cli, "check_schema_up_to_date", fake_check_schema_up_to_date)
    monkeypatch.setattr(cli, "run_simple", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "9999_fake" in capsys.readouterr().err


def test_main_wizard_on_complete_schedules_a_delayed_restart(monkeypatch, test_database_url):
    """Once gunicorn (multi-process) replaced the single-process dev server,
    on_complete can no longer hot-swap an in-process AppSwitcher -- it has to
    exit so Docker Compose's restart policy brings the container back up
    already configured. This proves that hand-off is wired, without ever
    actually killing the test process (os._exit is monkeypatched).
    """
    conn = psycopg.connect(test_database_url)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE wizard_state")
    conn.commit()
    conn.close()

    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    for var in (
        "DISCORD_BOT_TOKEN",
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "DISCORD_OAUTH_REDIRECT_URI",
        "DISCORD_TEST_GUILD_ID",
        "FLASK_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setattr(cli, "run_simple", lambda *args, **kwargs: None)

    captured = {}

    def fake_create_wizard_app(pool, *, on_complete=None, **kwargs):
        captured["on_complete"] = on_complete
        return object()

    monkeypatch.setattr(cli, "create_wizard_app", fake_create_wizard_app)

    timer_calls = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            timer_calls.append((delay, fn, args))

        def start(self):
            timer_calls[-1] = (*timer_calls[-1], "started")

    monkeypatch.setattr(cli.threading, "Timer", FakeTimer)
    monkeypatch.setattr(cli.os, "_exit", lambda code: captured.update(exited_with=code))

    cli.main()
    captured["on_complete"](object())

    assert len(timer_calls) == 1
    delay, fn, args, started = timer_calls[0]
    assert delay == cli.RESTART_DELAY_SECONDS
    assert args == (0,)
    assert started == "started"
    fn(*args)
    assert captured["exited_with"] == 0
