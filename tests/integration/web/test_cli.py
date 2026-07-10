"""Proves web/cli.py's own main() -- not just create_wizard_app()/
create_app() in isolation -- actually branches correctly: an unconfigured
install serves the wizard (ROADMAP.md §7's "first-run detection"), not a
crash. Monkeypatches dotenv.load_dotenv to a no-op so this test's outcome
never depends on whether a real, fully-populated .env happens to exist on
the machine running the suite (config.load_settings()/is_configured()
otherwise call the real load_dotenv(), which would silently fill in
"missing" env vars from a real repo .env if one exists) -- and monkeypatches
run_simple so this never binds a real socket (the hardcoded production port,
5000, is unreliable to bind in tests: confirmed firsthand elsewhere in this
project that macOS AirPlay Receiver squats it by default).
"""

import psycopg

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

    client = captured["app"].current.test_client()
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

    captured = {}

    class FakeApp:
        def run(self, host, port):
            captured["ran"] = True
            captured["host"] = host

    monkeypatch.setattr(cli, "create_app", lambda settings, pool: FakeApp())

    cli.main()

    assert captured.get("ran") is True
    assert captured["host"] == "127.0.0.1"


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

    class FakeApp:
        def run(self, host, port):
            captured["host"] = host

    monkeypatch.setattr(cli, "create_app", lambda settings, pool: FakeApp())

    cli.main()

    assert captured["host"] == "0.0.0.0"
