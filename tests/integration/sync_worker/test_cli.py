"""Proves sync_worker/cli.py's own main()/_run() refuse to start if the DB
schema is behind what the running code expects -- the sync worker's half of
the same fail-fast contract web/cli.py enforces (see
tests/integration/web/test_cli.py's schema_check tests). Never actually
connects to Discord: create_pool/ThreadbareClient are monkeypatched so this
only exercises the guard itself.

The _run_reset tests below need a real, committing connection rather than
the shared rollback-based db_conn fixture -- _run_reset opens its own pool
against the same test database, so seed data must actually be committed to
be visible to it, and results are read back from a second, separate
connection after it finishes. Matches the pattern already established in
tests/integration/sync_worker/test_backfill*.py for the same reason.
"""

import psycopg
import pytest
from psycopg.rows import dict_row

from threadbare.config import Settings
from threadbare.sync_worker import cli, repository


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


def _settings(database_url: str) -> Settings:
    return Settings(
        discord_bot_token="tok",
        discord_guild_id=1,
        database_url=database_url,
        discord_client_id="cid",
        discord_client_secret="secret",
        discord_oauth_redirect_uri="http://localhost:5000/oauth/callback",
        flask_secret_key="test-key",
    )


async def test_run_reset_resets_a_single_channel_and_its_threads(test_database_url):
    conn = await psycopg.AsyncConnection.connect(test_database_url, row_factory=dict_row)
    try:
        await conn.execute(
            "INSERT INTO guilds (id, name) VALUES (%s, %s)", (9001, "Reset Test Guild")
        )
        await conn.execute(
            "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, 'general')",
            (9010, 9001),
        )
        await conn.execute(
            "INSERT INTO threads (id, parent_channel_id, name, created_at) "
            "VALUES (%s, %s, %s, now())",
            (9020, 9010, "a thread"),
        )
        await repository.set_backfill_checkpoint(conn, 9010, last_message_id=500, complete=True)
        await repository.set_thread_backfill_checkpoint(
            conn, 9020, last_message_id=600, complete=True
        )
        await conn.commit()

        await cli._run_reset(_settings(test_database_url), channel_id=9010, reset_all=False)

        assert await repository.get_backfill_checkpoint(conn, 9010) is None
        assert await repository.get_thread_backfill_checkpoint(conn, 9020) is None
    finally:
        await conn.execute("DELETE FROM guilds WHERE id = %s", (9001,))
        await conn.commit()
        await conn.close()


async def test_run_reset_exits_when_channel_id_is_unknown(test_database_url, capsys):
    with pytest.raises(SystemExit) as exc_info:
        await cli._run_reset(_settings(test_database_url), channel_id=999999, reset_all=False)

    assert exc_info.value.code == 1
    assert "999999" in capsys.readouterr().err


async def test_run_reset_all_channels_resets_every_non_category_channel(test_database_url):
    conn = await psycopg.AsyncConnection.connect(test_database_url, row_factory=dict_row)
    try:
        await conn.execute(
            "INSERT INTO guilds (id, name) VALUES (%s, %s)", (9002, "Reset All Test Guild")
        )
        await conn.execute(
            "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, 'general')",
            (9011, 9002),
        )
        await conn.execute(
            "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, 'random')",
            (9012, 9002),
        )
        await conn.execute(
            "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 4, 'a category')",
            (9013, 9002),
        )
        await repository.set_backfill_checkpoint(conn, 9011, last_message_id=500, complete=True)
        await repository.set_backfill_checkpoint(conn, 9012, last_message_id=600, complete=True)
        await conn.commit()

        await cli._run_reset(_settings(test_database_url), channel_id=None, reset_all=True)

        assert await repository.get_backfill_checkpoint(conn, 9011) is None
        assert await repository.get_backfill_checkpoint(conn, 9012) is None
    finally:
        await conn.execute("DELETE FROM guilds WHERE id = %s", (9002,))
        await conn.commit()
        await conn.close()
