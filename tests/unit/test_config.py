import os

import pytest

from threadbare.config import (
    ConfigError,
    Settings,
    get_database_url,
    is_configured,
    load_settings,
    reload_env_file,
)

VALID_ENV = {
    "DISCORD_BOT_TOKEN": "fake-token",
    "DISCORD_TEST_GUILD_ID": "1524883720350466048",
    "DATABASE_URL": "postgresql://threadbare:threadbare@localhost:5432/threadbare_dev",
    "DISCORD_CLIENT_ID": "fake-client-id",
    "DISCORD_CLIENT_SECRET": "fake-client-secret",
    "DISCORD_OAUTH_REDIRECT_URI": "http://localhost:5000/oauth/callback",
    "FLASK_SECRET_KEY": "fake-secret-key",
}


def test_load_settings_returns_populated_settings():
    settings = load_settings(VALID_ENV)

    assert settings == Settings(
        discord_bot_token="fake-token",
        discord_guild_id=1524883720350466048,
        database_url="postgresql://threadbare:threadbare@localhost:5432/threadbare_dev",
        discord_client_id="fake-client-id",
        discord_client_secret="fake-client-secret",
        discord_oauth_redirect_uri="http://localhost:5000/oauth/callback",
        flask_secret_key="fake-secret-key",
    )


def test_load_settings_raises_on_single_missing_var():
    env = dict(VALID_ENV)
    del env["DISCORD_BOT_TOKEN"]

    with pytest.raises(ConfigError, match="DISCORD_BOT_TOKEN"):
        load_settings(env)


def test_load_settings_reports_all_missing_vars_at_once():
    with pytest.raises(ConfigError) as exc_info:
        load_settings({})

    message = str(exc_info.value)
    assert "DISCORD_BOT_TOKEN" in message
    assert "DISCORD_TEST_GUILD_ID" in message
    assert "DATABASE_URL" in message
    assert "DISCORD_CLIENT_ID" in message
    assert "DISCORD_CLIENT_SECRET" in message
    assert "DISCORD_OAUTH_REDIRECT_URI" in message
    assert "FLASK_SECRET_KEY" in message


def test_load_settings_rejects_non_integer_guild_id():
    env = dict(VALID_ENV, DISCORD_TEST_GUILD_ID="not-a-snowflake")

    with pytest.raises(ConfigError, match="DISCORD_TEST_GUILD_ID"):
        load_settings(env)


def test_load_settings_rejects_blank_bot_token():
    env = dict(VALID_ENV, DISCORD_BOT_TOKEN="   ")

    with pytest.raises(ConfigError, match="DISCORD_BOT_TOKEN"):
        load_settings(env)


def test_load_settings_rejects_blank_discord_client_id():
    env = dict(VALID_ENV, DISCORD_CLIENT_ID="   ")

    with pytest.raises(ConfigError, match="DISCORD_CLIENT_ID"):
        load_settings(env)


def test_load_settings_rejects_blank_discord_client_secret():
    env = dict(VALID_ENV, DISCORD_CLIENT_SECRET="   ")

    with pytest.raises(ConfigError, match="DISCORD_CLIENT_SECRET"):
        load_settings(env)


def test_load_settings_rejects_blank_oauth_redirect_uri():
    env = dict(VALID_ENV, DISCORD_OAUTH_REDIRECT_URI="   ")

    with pytest.raises(ConfigError, match="DISCORD_OAUTH_REDIRECT_URI"):
        load_settings(env)


def test_load_settings_rejects_blank_flask_secret_key():
    env = dict(VALID_ENV, FLASK_SECRET_KEY="   ")

    with pytest.raises(ConfigError, match="FLASK_SECRET_KEY"):
        load_settings(env)


def test_get_database_url_returns_value_when_present():
    env = {"DATABASE_URL": "postgresql://threadbare:threadbare@localhost:5432/threadbare_dev"}

    assert get_database_url(env) == "postgresql://threadbare:threadbare@localhost:5432/threadbare_dev"


def test_get_database_url_raises_when_missing():
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        get_database_url({})


def test_is_configured_true_for_full_env():
    assert is_configured(VALID_ENV) is True


def test_is_configured_false_when_discord_bot_token_missing():
    env = dict(VALID_ENV)
    del env["DISCORD_BOT_TOKEN"]

    assert is_configured(env) is False


def test_reload_env_file_fills_blank_env_var_from_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("DISCORD_BOT_TOKEN=real-token-from-file\n")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")

    reload_env_file(env_path)

    assert os.environ["DISCORD_BOT_TOKEN"] == "real-token-from-file"


def test_reload_env_file_leaves_non_blank_env_var_untouched(tmp_path, monkeypatch):
    # Simulates docker-compose.yml's `environment:` block setting DATABASE_URL
    # directly (which always wins over `env_file:` at container creation) --
    # reload_env_file must never let the file's value clobber it.
    env_path = tmp_path / ".env"
    env_path.write_text("DATABASE_URL=postgresql://should-not-be-used\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql://real-container-db")

    reload_env_file(env_path)

    assert os.environ["DATABASE_URL"] == "postgresql://real-container-db"


def test_reload_env_file_fills_genuinely_absent_env_var(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("FLASK_SECRET_KEY=from-file\n")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)

    reload_env_file(env_path)

    assert os.environ["FLASK_SECRET_KEY"] == "from-file"
