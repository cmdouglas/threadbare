"""Checkpointed channel backfill. Orchestration is written against two small
Protocols (HistoryFetcher, BackfillSink) rather than discord.py/psycopg
directly, so the paging/checkpoint/resume logic is unit-testable with
in-memory fakes — no live gateway connection or real database required.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

import discord

from threadbare.sync_worker import repository, transform
from threadbare.sync_worker.checkpoints import advance_backfill_progress
from threadbare.sync_worker.discord_types import MessageLike
from threadbare.sync_worker.permissions import should_sync

SKIPPED_CHANNEL_TYPES = (discord.ChannelType.category, discord.ChannelType.forum)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100
ATTACHMENT_URL_TTL = timedelta(hours=24)
DEFAULT_MAX_CONCURRENCY = 3
DEFAULT_MAX_RETRIES = 3
SLOW_WAIT_LOG_THRESHOLD_SECONDS = 1.0


class HistoryFetcher(Protocol):
    async def fetch_batch(
        self, *, channel_id: int, after: int | None, limit: int
    ) -> list[MessageLike]: ...


class BackfillSink(Protocol):
    async def get_checkpoint(self, channel_id: int) -> int | None: ...

    async def write_message(
        self, message: MessageLike, *, channel_id: int | None = None, thread_id: int | None = None
    ) -> None: ...

    async def set_checkpoint(
        self, channel_id: int, *, last_message_id: int | None, complete: bool
    ) -> None: ...

    async def commit(self) -> None: ...


def _estimate_attachment_url_expiry() -> datetime:
    # Discord's signed CDN URLs expire in ~24h (DESIGN.md §3); we don't parse
    # the signature's exact expiry, just estimate conservatively. Refreshing
    # an expired URL is the web app's /att/{id} proxy job, not the sync
    # worker's — see DESIGN.md §4.
    return datetime.now(UTC) + ATTACHMENT_URL_TTL


class DiscordHistoryFetcher:
    """The real HistoryFetcher, wrapping a discord.py client's channel
    history. discord.Message objects structurally satisfy MessageLike, so no
    adaptation is needed beyond the fetch call itself.
    """

    def __init__(self, client: discord.Client):
        self._client = client

    async def fetch_batch(
        self, *, channel_id: int, after: int | None, limit: int
    ) -> list[MessageLike]:
        channel = self._client.get_channel(channel_id) or await self._client.fetch_channel(
            channel_id
        )
        after_obj = discord.Object(id=after) if after is not None else None
        return [
            message
            async for message in channel.history(after=after_obj, limit=limit, oldest_first=True)
        ]


class BoundedHistoryFetcher:
    """Wraps any HistoryFetcher with a cap on concurrent in-flight fetch
    calls. discord.py's HTTPClient already honors rate-limit headers and
    backs off automatically — this is a different concern, bounding how
    many requests we allow in flight at once so backfilling/reconciling many
    channels concurrently (once a multi-channel orchestrator exists; see
    ROADMAP.md) doesn't outpace discord.py's own bucket prediction.
    """

    def __init__(self, fetcher: HistoryFetcher, *, max_concurrency: int = DEFAULT_MAX_CONCURRENCY):
        self._fetcher = fetcher
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_batch(
        self, *, channel_id: int, after: int | None, limit: int
    ) -> list[MessageLike]:
        async with self._semaphore:
            return await self._fetcher.fetch_batch(channel_id=channel_id, after=after, limit=limit)


class RetryingHistoryFetcher:
    """Wraps any HistoryFetcher, retrying with backoff specifically on
    discord.RateLimited — raised only when discord.py's own internal wait
    would exceed its configured max_ratelimit_timeout, so it gives up and
    surfaces the error rather than blocking forever. Ordinary 429s are
    already handled transparently inside discord.py's HTTPClient; this is
    the fallback for the case it explicitly can't. Any other exception
    propagates immediately, untouched.
    """

    def __init__(self, fetcher: HistoryFetcher, *, max_retries: int = DEFAULT_MAX_RETRIES):
        self._fetcher = fetcher
        self._max_retries = max_retries

    async def fetch_batch(
        self, *, channel_id: int, after: int | None, limit: int
    ) -> list[MessageLike]:
        attempt = 0
        while True:
            try:
                return await self._fetcher.fetch_batch(
                    channel_id=channel_id, after=after, limit=limit
                )
            except discord.RateLimited as e:
                attempt += 1
                if attempt > self._max_retries:
                    raise
                if e.retry_after > SLOW_WAIT_LOG_THRESHOLD_SECONDS:
                    logger.warning(
                        "Rate limited fetching channel %s, waiting %.1fs (attempt %d/%d)",
                        channel_id,
                        e.retry_after,
                        attempt,
                        self._max_retries,
                    )
                await asyncio.sleep(e.retry_after)


class RepositoryBackfillSink:
    """The real BackfillSink, wrapping repository.py against an open
    connection. Callers own the connection's transaction boundary.
    """

    def __init__(self, conn):
        self._conn = conn

    async def get_checkpoint(self, channel_id: int) -> int | None:
        return await repository.get_backfill_checkpoint(self._conn, channel_id)

    async def write_message(
        self, message: MessageLike, *, channel_id: int | None = None, thread_id: int | None = None
    ) -> None:
        await repository.upsert_user(self._conn, transform.user_to_row(message.author))
        await repository.upsert_message(
            self._conn,
            transform.message_to_row(message, channel_id=channel_id, thread_id=thread_id),
        )
        for attachment in message.attachments:
            await repository.upsert_attachment(
                self._conn,
                transform.attachment_to_row(
                    attachment,
                    message_id=message.id,
                    url_expires_at=_estimate_attachment_url_expiry(),
                ),
            )

    async def set_checkpoint(
        self, channel_id: int, *, last_message_id: int | None, complete: bool
    ) -> None:
        await repository.set_backfill_checkpoint(
            self._conn, channel_id, last_message_id=last_message_id, complete=complete
        )

    async def commit(self) -> None:
        await self._conn.commit()


async def backfill_channel(
    fetcher: HistoryFetcher,
    sink: BackfillSink,
    *,
    channel_id: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Walk a channel's history forward from its last checkpoint (or the
    beginning, if none), writing messages in batches and persisting a
    checkpoint after each one so a restart resumes near where it left off.
    Writes are idempotent (upserts), so re-processing overlapping messages
    on resume is harmless. Returns the number of messages written.
    """
    after = await sink.get_checkpoint(channel_id)
    total_written = 0

    while True:
        batch = await fetcher.fetch_batch(channel_id=channel_id, after=after, limit=batch_size)

        for message in batch:
            await sink.write_message(message, channel_id=channel_id)
            total_written += 1

        progress = advance_backfill_progress(
            batch_message_ids=[m.id for m in batch], requested_limit=batch_size
        )
        # An empty final page shouldn't clobber a real checkpoint with None.
        checkpoint = progress.last_message_id if progress.last_message_id is not None else after
        await sink.set_checkpoint(
            channel_id, last_message_id=checkpoint, complete=progress.complete
        )
        await sink.commit()
        after = checkpoint

        if progress.complete:
            break

    return total_written


async def backfill_guild(
    client: discord.Client,
    pool,
    *,
    guild_id: int,
    max_channel_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    batch_size: int = DEFAULT_BATCH_SIZE,
    fetcher: HistoryFetcher | None = None,
) -> None:
    """Backfill every in-scope channel in a guild, concurrently. Each
    channel holds its own pool connection for the duration of its backfill
    (a RepositoryBackfillSink holds its connection the whole time, not just
    one batch), bounded by max_channel_concurrency — a different resource
    than the Discord-call concurrency BoundedHistoryFetcher caps, which is
    why both exist. Skips categories and forum channels (no top-level
    history; ROADMAP-flagged, not handled here) and anything not currently
    is_public+indexed.

    `fetcher` defaults to the real hardened chain
    (Retrying(Bounded(Discord))) built once and shared across all channels,
    so the concurrency cap is real across the whole guild, not per-channel.
    Injectable for testing without a live gateway connection.
    """
    guild = client.get_guild(guild_id) or await client.fetch_guild(guild_id)
    channels = await guild.fetch_channels()
    if fetcher is None:
        fetcher = RetryingHistoryFetcher(BoundedHistoryFetcher(DiscordHistoryFetcher(client)))
    semaphore = asyncio.Semaphore(max_channel_concurrency)

    async def _backfill_one(channel_id: int) -> None:
        async with semaphore, pool.connection() as conn:
            flags = await repository.get_channel_sync_flags(conn, channel_id)
            if flags is None or not should_sync(is_public=flags[0], indexed=flags[1]):
                return
            sink = RepositoryBackfillSink(conn)
            await backfill_channel(fetcher, sink, channel_id=channel_id, batch_size=batch_size)

    candidates = [channel.id for channel in channels if channel.type not in SKIPPED_CHANNEL_TYPES]
    await asyncio.gather(*(_backfill_one(channel_id) for channel_id in candidates))
