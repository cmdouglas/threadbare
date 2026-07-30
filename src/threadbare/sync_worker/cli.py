import argparse
import asyncio
import sys

import threadbare
from threadbare.config import ConfigError, load_settings
from threadbare.db import grouping
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


async def _run_regroup(settings, *, channel_id: int | None, regroup_all: bool) -> None:
    """Recompute post grouping now, rather than waiting for the nightly sweep
    to notice the generation stamp is stale. The admin page's Regroup button
    does the same work through the sync_jobs queue; this is the escape hatch
    for a deployment where the web UI isn't reachable.
    """
    await check_schema_up_to_date(settings.database_url)
    pool = create_pool(settings.database_url)
    await pool.open()
    try:
        async with pool.connection() as conn:
            if regroup_all:
                channel_ids = await repository.get_content_channel_ids(conn)
            else:
                if not await repository.channel_exists(conn, channel_id):
                    print(f"No channel with id {channel_id} found.", file=sys.stderr)
                    raise SystemExit(1)
                channel_ids = [channel_id]
            generation = await repository.get_grouping_generation(conn)
            gap_seconds = await repository.get_merge_gap_seconds(conn)

        changed = 0
        for cid in channel_ids:
            # One transaction per channel, matching reconciliation's own
            # sweep: a failure part-way leaves the channels already stamped
            # alone rather than rolling all of them back.
            async with pool.connection() as conn:
                changed += await grouping.regroup_channel_and_threads(
                    conn, channel_id=cid, gap_seconds=gap_seconds
                )
                await repository.stamp_grouping_generation(conn, cid, generation=generation)

        print(
            f"Regrouped {len(channel_ids)} channel(s); {changed} message(s) changed post. "
            "No restart needed -- the web app reads this on the next page load."
        )
    finally:
        await pool.close()


def _build_parser() -> argparse.ArgumentParser:
    """argparse rather than hand-rolled argv scanning: the previous version
    open-coded `argv.index("--reset-channel")` plus `int(argv[idx + 1])`, its own
    mutual-exclusion check, and a separate `"--version" in argv` scan -- all of
    which argparse does, including `--help`, which there wasn't one of before.
    """
    parser = argparse.ArgumentParser(
        prog="threadbare-sync-worker",
        description="Mirror a Discord guild's history into Postgres.",
    )
    parser.add_argument(
        "--version", action="version", version=f"threadbare {threadbare.__version__}"
    )
    # One group: every one of these is a "do this maintenance job and exit"
    # mode, and combining two of them in a single invocation has no meaning.
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument(
        "--reset-channel",
        type=int,
        metavar="CHANNEL_ID",
        help="Clear one channel's backfill checkpoint, then exit.",
    )
    maintenance.add_argument(
        "--reset-all-channels",
        action="store_true",
        help="Clear every channel's backfill checkpoint, then exit.",
    )
    maintenance.add_argument(
        "--regroup-channel",
        type=int,
        metavar="CHANNEL_ID",
        help="Recompute one channel's post grouping, then exit.",
    )
    maintenance.add_argument(
        "--regroup-all",
        action="store_true",
        help="Recompute every channel's post grouping, then exit.",
    )
    return parser


def _parse_maintenance_flags(argv: list[str]) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def main() -> None:
    args = _parse_maintenance_flags(sys.argv[1:])

    configure_logging()

    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e

    try:
        if args.reset_channel is not None or args.reset_all_channels:
            asyncio.run(
                _run_reset(
                    settings,
                    channel_id=args.reset_channel,
                    reset_all=args.reset_all_channels,
                )
            )
        elif args.regroup_channel is not None or args.regroup_all:
            asyncio.run(
                _run_regroup(
                    settings,
                    channel_id=args.regroup_channel,
                    regroup_all=args.regroup_all,
                )
            )
        else:
            asyncio.run(_run(settings))
    except MigrationError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
