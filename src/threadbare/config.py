import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    pass


DEFAULT_THEME_STORAGE_DIR = "/app/theme_storage"


@dataclass(frozen=True)
class Settings:
    discord_bot_token: str
    discord_guild_id: int
    database_url: str
    discord_client_id: str
    discord_client_secret: str
    discord_oauth_redirect_uri: str
    flask_secret_key: str
    # Where custom-theme bundles are extracted and served from (a Docker
    # named volume mounted into the web container). Has a default so it isn't
    # a newly-required env var for existing installs; the volume must be
    # backed up separately (DESIGN.md §9) -- the DB dump doesn't capture it.
    theme_storage_dir: str = DEFAULT_THEME_STORAGE_DIR


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


# Every required env var, mapped to the Settings field it populates. A table
# rather than seven near-identical get/strip/append blocks, so adding a
# required setting is one line.
_REQUIRED_STR_SETTINGS = (
    ("DISCORD_BOT_TOKEN", "discord_bot_token"),
    ("DATABASE_URL", "database_url"),
    ("DISCORD_CLIENT_ID", "discord_client_id"),
    ("DISCORD_CLIENT_SECRET", "discord_client_secret"),
    ("DISCORD_OAUTH_REDIRECT_URI", "discord_oauth_redirect_uri"),
    ("FLASK_SECRET_KEY", "flask_secret_key"),
)

# Renamed from DISCORD_TEST_GUILD_ID: nothing about a deployment's own guild is
# a test. Named here so the missing-config error can point an upgrading
# operator straight at the .env line they need to change -- this is a hard
# rename with no fallback (DESIGN.md §7 records it as a deliberate departure
# from the upgrade contract's "safe default or guided upgrade step" rule), so a
# clear error is the whole of the guided upgrade step.
GUILD_ID_ENV_VAR = "DISCORD_GUILD_ID"
_RENAMED_GUILD_ID_ENV_VAR = "DISCORD_TEST_GUILD_ID"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        reload_env_file()
        env = os.environ

    errors: list[str] = []
    values: dict[str, str] = {}

    for env_var, field in _REQUIRED_STR_SETTINGS:
        value = env.get(env_var, "").strip()
        if not value:
            errors.append(f"{env_var} is required")
        values[field] = value

    raw_guild_id = env.get(GUILD_ID_ENV_VAR, "").strip()
    guild_id: int | None = None
    if not raw_guild_id:
        message = f"{GUILD_ID_ENV_VAR} is required"
        if env.get(_RENAMED_GUILD_ID_ENV_VAR, "").strip():
            message += (
                f" -- it was renamed from {_RENAMED_GUILD_ID_ENV_VAR}, which is still set in "
                f"this environment. Rename that line in your .env to {GUILD_ID_ENV_VAR}."
            )
        errors.append(message)
    else:
        try:
            guild_id = int(raw_guild_id)
        except ValueError:
            errors.append(f"{GUILD_ID_ENV_VAR} must be an integer, got {raw_guild_id!r}")

    if errors:
        raise ConfigError("Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))

    theme_storage_dir = env.get("THEME_STORAGE_DIR", "").strip() or DEFAULT_THEME_STORAGE_DIR

    assert guild_id is not None
    return Settings(
        discord_guild_id=guild_id,
        theme_storage_dir=theme_storage_dir,
        **values,
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
