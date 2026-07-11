"""Proves sync_worker/cli.py's own main()/_run() refuse to start if the DB
schema is behind what the running code expects -- the sync worker's half of
the same fail-fast contract web/cli.py enforces (see
tests/integration/web/test_cli.py's schema_check tests). Never actually
connects to Discord: create_pool/ThreadbareClient are monkeypatched so this
only exercises the guard itself.
"""

import pytest

from threadbare.sync_worker import cli


def test_main_exits_when_schema_check_fails(monkeypatch, test_database_url, capsys):
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

    def fail_if_called(*args, **kwargs):
        raise AssertionError("create_pool should never be reached when the schema check fails")

    monkeypatch.setattr(cli, "create_pool", fail_if_called)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "9999_fake" in capsys.readouterr().err


def test_main_proceeds_past_schema_check_when_up_to_date(monkeypatch, test_database_url):
    # Real check_schema_up_to_date against the real (fully-migrated) test
    # database -- proves the guard is a no-op in the normal case, not just
    # that a fake failure is caught. create_pool is still faked so this
    # never opens a real pool or touches Discord.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CLIENT_ID", "cid")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DISCORD_OAUTH_REDIRECT_URI", "http://localhost:5000/oauth/callback")
    monkeypatch.setenv("DISCORD_TEST_GUILD_ID", "1")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-key")

    reached = {}

    def fake_create_pool(dsn):
        reached["create_pool"] = True
        raise RuntimeError("stop here -- proves we got past the schema check")

    monkeypatch.setattr(cli, "create_pool", fake_create_pool)

    with pytest.raises(RuntimeError, match="stop here"):
        cli.main()

    assert reached.get("create_pool") is True
