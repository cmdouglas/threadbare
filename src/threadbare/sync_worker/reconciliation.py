"""Nightly reconciliation: re-walk each channel's recent history and
converge local state with it, repairing anything a gateway outage missed —
creates and edits (upserting the fetched batch repairs both, since a fetched
message always reflects current content) and deletes (any locally-stored id
in the window that the fetch no longer returns).

Uses a fixed lookback window per sweep (not "since last reconciliation"), so
consecutive sweeps overlap generously and a missed sweep or two doesn't
create a gap — simpler and more self-healing than precise incremental
tracking.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import discord

from threadbare.sync_worker import repository
from threadbare.sync_worker.backfill import (
    DEFAULT_BATCH_SIZE,
    BoundedHistoryFetcher,
    DiscordHistoryFetcher,
    HistoryFetcher,
    RepositoryBackfillSink,
    RetryingHistoryFetcher,
)
from threadbare.sync_worker.checkpoints import advance_backfill_progress
from threadbare.sync_worker.discord_types import MessageLike
from threadbare.sync_worker.permissions import should_sync

DEFAULT_LOOKBACK = timedelta(hours=24)
DEFAULT_RECONCILIATION_HOUR = 3


def diff_message_sets(local_ids: set[int], remote_ids: set[int]) -> set[int]:
    """Ids present locally but absent from a fresh fetch of the same window
    — a gateway-outage delete that was missed.
    """
    return local_ids - remote_ids


def next_run_at(now: datetime, hour: int) -> datetime:
    """The next occurrence of `hour` (same tzinfo as `now`) strictly after
    `now` — today if that hour hasn't passed yet, otherwise tomorrow. Always
    strictly future, so a scheduling loop never computes a zero/negative
    sleep when invoked exactly at the target hour.
    """
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


@dataclass(frozen=True)
class ReconciliationResult:
    upserted: int
    deleted: int


class ReconciliationSink(Protocol):
    async def write_message(self, message: MessageLike, *, channel_id: int) -> None: ...

    async def local_message_ids_since(self, channel_id: int, after: int) -> set[int]: ...

    async def delete_messages(self, message_ids: list[int]) -> None: ...

    async def mark_reconciled(self, channel_id: int) -> None: ...


class RepositoryReconciliationSink:
    """The real ReconciliationSink. Reuses RepositoryBackfillSink for writes
    — an upsert is an upsert whether it's backfilling or reconciling.
    """

    def __init__(self, conn):
        self._conn = conn
        self._writer = RepositoryBackfillSink(conn)

    async def write_message(self, message: MessageLike, *, channel_id: int) -> None:
        await self._writer.write_message(message, channel_id=channel_id)

    async def local_message_ids_since(self, channel_id: int, after: int) -> set[int]:
        return await repository.get_message_ids_since(self._conn, channel_id, after)

    async def delete_messages(self, message_ids: list[int]) -> None:
        await repository.delete_messages(self._conn, message_ids)

    async def mark_reconciled(self, channel_id: int) -> None:
        await repository.mark_channel_reconciled(self._conn, channel_id)


async def reconcile_channel(
    fetcher: HistoryFetcher,
    sink: ReconciliationSink,
    *,
    channel_id: int,
    after: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ReconciliationResult:
    """Re-walk a channel's history from `after` (a snowflake lookback cutoff,
    not a stored checkpoint — reconciliation always re-covers the same
    trailing window rather than resuming from where it last stopped) and
    converge local state with what's actually there.
    """
    remote_ids: set[int] = set()
    cursor = after

    while True:
        batch = await fetcher.fetch_batch(channel_id=channel_id, after=cursor, limit=batch_size)

        for message in batch:
            await sink.write_message(message, channel_id=channel_id)
            remote_ids.add(message.id)

        progress = advance_backfill_progress(
            batch_message_ids=[m.id for m in batch], requested_limit=batch_size
        )
        if progress.complete:
            break
        cursor = progress.last_message_id

    local_ids = await sink.local_message_ids_since(channel_id, after)
    stale_ids = diff_message_sets(local_ids, remote_ids)
    if stale_ids:
        await sink.delete_messages(list(stale_ids))

    await sink.mark_reconciled(channel_id)
    return ReconciliationResult(upserted=len(remote_ids), deleted=len(stale_ids))


def lookback_cursor(now: datetime, lookback: timedelta) -> int:
    """The snowflake `after` cursor for a reconciliation sweep's lookback
    window. discord.py's time_snowflake does the timestamp<->snowflake
    conversion — we don't hand-roll it (see the sync worker plan's note on
    not reimplementing what discord.py already gets right).
    """
    return discord.utils.time_snowflake(now - lookback)


async def reconcile_guild(
    client: discord.Client,
    pool,
    *,
    guild_id: int,
    lookback: timedelta = DEFAULT_LOOKBACK,
    now: datetime | None = None,
) -> None:
    """Reconcile every in-scope channel in a guild. Skips categories (no
    messages of their own) and any channel that isn't currently public+
    indexed — reconciling a gated channel would mean actively re-adding
    content we're supposed to be keeping out, the opposite of what
    reconciliation is for.
    """
    guild = client.get_guild(guild_id) or await client.fetch_guild(guild_id)
    channels = await guild.fetch_channels()
    after = lookback_cursor(now or datetime.now(UTC), lookback)
    fetcher = RetryingHistoryFetcher(BoundedHistoryFetcher(DiscordHistoryFetcher(client)))

    for channel in channels:
        if channel.type is discord.ChannelType.category:
            continue
        async with pool.connection() as conn:
            flags = await repository.get_channel_sync_flags(conn, channel.id)
            if flags is None or not should_sync(is_public=flags[0], indexed=flags[1]):
                continue
            sink = RepositoryReconciliationSink(conn)
            await reconcile_channel(fetcher, sink, channel_id=channel.id, after=after)


async def reconciliation_loop(
    client: discord.Client,
    pool,
    *,
    guild_id: int,
    hour: int = DEFAULT_RECONCILIATION_HOUR,
    lookback: timedelta = DEFAULT_LOOKBACK,
) -> None:
    """Runs reconcile_guild immediately on startup (catch-up after any
    downtime), then nightly at `hour` thereafter. Runs forever — intended as
    a background asyncio task for the sync worker's lifetime.
    """
    while True:
        await reconcile_guild(client, pool, guild_id=guild_id, lookback=lookback)
        now = datetime.now(UTC)
        await asyncio.sleep((next_run_at(now, hour) - now).total_seconds())
