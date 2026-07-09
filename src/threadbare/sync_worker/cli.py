import sys

from threadbare.config import ConfigError, load_settings
from threadbare.sync_worker.bot import ThreadbareClient


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e

    client = ThreadbareClient(guild_id=settings.discord_guild_id)
    client.run(settings.discord_bot_token)


if __name__ == "__main__":
    main()
