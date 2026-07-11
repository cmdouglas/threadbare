import asyncio
import sys

import threadbare
from threadbare.config import ConfigError, load_settings
from threadbare.db.migrate import MigrationError, check_schema_up_to_date
from threadbare.db.pool import create_pool
from threadbare.sync_worker.bot import ThreadbareClient


async def _run(settings) -> None:
    await check_schema_up_to_date(settings.database_url)
    pool = create_pool(settings.database_url)
    await pool.open()
    try:
        client = ThreadbareClient(guild_id=settings.discord_guild_id, pool=pool)
        async with client:
            await client.start(settings.discord_bot_token)
    finally:
        await pool.close()


def main() -> None:
    if "--version" in sys.argv[1:]:
        print(f"threadbare {threadbare.__version__}")
        raise SystemExit(0)

    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e

    try:
        asyncio.run(_run(settings))
    except MigrationError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
