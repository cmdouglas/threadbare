import pytest

from threadbare.config import ConfigError, Settings, load_settings

VALID_ENV = {
    "DISCORD_BOT_TOKEN": "fake-token",
    "DISCORD_TEST_GUILD_ID": "1524883720350466048",
    "DATABASE_URL": "postgresql://threadbare:threadbare@localhost:5432/threadbare_dev",
}


def test_load_settings_returns_populated_settings():
    settings = load_settings(VALID_ENV)

    assert settings == Settings(
        discord_bot_token="fake-token",
        discord_guild_id=1524883720350466048,
        database_url="postgresql://threadbare:threadbare@localhost:5432/threadbare_dev",
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


def test_load_settings_rejects_non_integer_guild_id():
    env = dict(VALID_ENV, DISCORD_TEST_GUILD_ID="not-a-snowflake")

    with pytest.raises(ConfigError, match="DISCORD_TEST_GUILD_ID"):
        load_settings(env)


def test_load_settings_rejects_blank_bot_token():
    env = dict(VALID_ENV, DISCORD_BOT_TOKEN="   ")

    with pytest.raises(ConfigError, match="DISCORD_BOT_TOKEN"):
        load_settings(env)
