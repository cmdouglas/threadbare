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


def reload_env_file(dotenv_path: str | os.PathLike | None = None) -> None:
    """Loads a .env file into os.environ, treating a key that's *present but
    blank* the same as one that's absent -- unlike plain load_dotenv()'s
    default (override=False), which only fills in genuinely absent keys.

    This matters because docker-compose.yml's `env_file: - .env` bakes
    .env.example's empty Discord-config placeholders into the container's
    environment at container-creation time; when the setup wizard later
    writes real values into the on-disk .env file and the container
    restarts (web/cli.py's on_complete + `restart: unless-stopped`), the new
    process's os.environ still has those original blank values, and plain
    load_dotenv() would never pick up the real ones now on disk.

    Deliberately does NOT use load_dotenv(override=True): that would also
    clobber keys that hold a real, non-blank value for a reason unrelated to
    this file -- e.g. DATABASE_URL, which docker-compose.yml sets directly
    via `environment:` (always taking precedence over `env_file:`) while
    .env itself may still carry .env.example's local-dev default. Only
    blank-vs-absent is treated as equivalent; a real value already in
    os.environ is never overwritten.

    Deletes blank keys first and leans on load_dotenv()'s own
    fill-only-absent default, rather than also calling dotenv_values() to
    inspect the file's contents directly -- tests that need to suppress
    file-based env loading only ever have to mock the one function
    (load_dotenv) they already know about (see test_cli.py's docstring).
    """
    from dotenv import load_dotenv

    for key in [k for k, v in os.environ.items() if not v.strip()]:
        del os.environ[key]
    load_dotenv(dotenv_path=dotenv_path)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        reload_env_file()
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


def get_database_url(env: Mapping[str, str] | None = None) -> str:
    """DATABASE_URL alone, raising ConfigError only for this one var --
    unlike load_settings(), which is all-or-nothing across every Discord
    config value too. Used by web/cli.py's wizard-mode boot path:
    DATABASE_URL is assumed always present (container-network Postgres, not
    something a mod hand-enters -- DESIGN.md §8), so wizard mode can reach
    Postgres to persist its own progress even before any Discord config
    exists.
    """
    if env is None:
        reload_env_file()
        env = os.environ

    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError("Invalid configuration:\n  - DATABASE_URL is required")
    return database_url


def is_configured(env: Mapping[str, str] | None = None) -> bool:
    """True iff load_settings(env) would succeed -- web/cli.py's branch
    point between wizard mode and normal mode.
    """
    try:
        load_settings(env)
    except ConfigError:
        return False
    return True
