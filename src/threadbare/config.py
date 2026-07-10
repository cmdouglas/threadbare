import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    discord_bot_token: str
    discord_guild_id: int
    database_url: str
    discord_client_id: str
    discord_client_secret: str
    discord_oauth_redirect_uri: str
    flask_secret_key: str


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        from dotenv import load_dotenv

        load_dotenv()
        env = os.environ

    errors: list[str] = []

    bot_token = env.get("DISCORD_BOT_TOKEN", "").strip()
    if not bot_token:
        errors.append("DISCORD_BOT_TOKEN is required")

    raw_guild_id = env.get("DISCORD_TEST_GUILD_ID", "").strip()
    guild_id: int | None = None
    if not raw_guild_id:
        errors.append("DISCORD_TEST_GUILD_ID is required")
    else:
        try:
            guild_id = int(raw_guild_id)
        except ValueError:
            errors.append(f"DISCORD_TEST_GUILD_ID must be an integer, got {raw_guild_id!r}")

    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        errors.append("DATABASE_URL is required")

    client_id = env.get("DISCORD_CLIENT_ID", "").strip()
    if not client_id:
        errors.append("DISCORD_CLIENT_ID is required")

    client_secret = env.get("DISCORD_CLIENT_SECRET", "").strip()
    if not client_secret:
        errors.append("DISCORD_CLIENT_SECRET is required")

    oauth_redirect_uri = env.get("DISCORD_OAUTH_REDIRECT_URI", "").strip()
    if not oauth_redirect_uri:
        errors.append("DISCORD_OAUTH_REDIRECT_URI is required")

    flask_secret_key = env.get("FLASK_SECRET_KEY", "").strip()
    if not flask_secret_key:
        errors.append("FLASK_SECRET_KEY is required")

    if errors:
        raise ConfigError("Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))

    assert guild_id is not None
    return Settings(
        discord_bot_token=bot_token,
        discord_guild_id=guild_id,
        database_url=database_url,
        discord_client_id=client_id,
        discord_client_secret=client_secret,
        discord_oauth_redirect_uri=oauth_redirect_uri,
        flask_secret_key=flask_secret_key,
    )
