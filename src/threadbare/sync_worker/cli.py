import asyncio
import sys

from threadbare.config import ConfigError, load_settings
from threadbare.db.pool import create_pool
from threadbare.sync_worker.bot import ThreadbareClient


async def _run(settings) -> None:
    pool = create_pool(settings.database_url)
    await pool.open()
    try:
        client = ThreadbareClient(guild_id=settings.discord_guild_id, pool=pool)
        async with client:
            await client.start(settings.discord_bot_token)
    finally:
        await pool.close()


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e

    asyncio.run(_run(settings))


if __name__ == "__main__":
    main()
