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

    if errors:
        raise ConfigError("Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))

    assert guild_id is not None
    return Settings(
        discord_bot_token=bot_token,
        discord_guild_id=guild_id,
        database_url=database_url,
    )
