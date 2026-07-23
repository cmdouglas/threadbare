import asyncio
import sys

import threadbare
from threadbare.config import ConfigError, load_settings
from threadbare.db.migrate import MigrationError, check_schema_up_to_date
from threadbare.db.pool import create_pool
from threadbare.logging_config import configure_logging
from threadbare.sync_worker import repository
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


async def _run_reset(settings, *, channel_id: int | None, reset_all: bool) -> None:
    await check_schema_up_to_date(settings.database_url)
    pool = create_pool(settings.database_url)
    await pool.open()
    try:
        async with pool.connection() as conn:
            if reset_all:
                channel_ids = await repository.get_content_channel_ids(conn)
            else:
                if not await repository.channel_exists(conn, channel_id):
                    print(f"No channel with id {channel_id} found.", file=sys.stderr)
                    raise SystemExit(1)
                channel_ids = [channel_id]

            thread_count = 0
            for cid in channel_ids:
                await repository.set_backfill_checkpoint(
                    conn, cid, last_message_id=None, complete=False
                )
                thread_count += await repository.reset_thread_checkpoints_for_channel(conn, cid)

        print(
            f"Reset backfill checkpoint for {len(channel_ids)} channel(s) and "
            f"{thread_count} thread(s). Restart the sync worker "
            "(e.g. `docker compose restart sync-worker`) to re-walk full history from Discord."
        )
    finally:
        await pool.close()


def _parse_reset_flags(argv: list[str]) -> tuple[int | None, bool]:
    reset_all = "--reset-all-channels" in argv
    channel_id = None
    if "--reset-channel" in argv:
        idx = argv.index("--reset-channel")
        try:
            channel_id = int(argv[idx + 1])
        except (IndexError, ValueError):
            print("--reset-channel requires a numeric channel id", file=sys.stderr)
            raise SystemExit(1) from None

    if channel_id is not None and reset_all:
        print("--reset-channel and --reset-all-channels are mutually exclusive", file=sys.stderr)
        raise SystemExit(1)

    return channel_id, reset_all


def main() -> None:
    argv = sys.argv[1:]

    if "--version" in argv:
        print(f"threadbare {threadbare.__version__}")
        raise SystemExit(0)

    channel_id, reset_all = _parse_reset_flags(argv)

    configure_logging()

    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e

    try:
        if channel_id is not None or reset_all:
            asyncio.run(_run_reset(settings, channel_id=channel_id, reset_all=reset_all))
        else:
            asyncio.run(_run(settings))
    except MigrationError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
