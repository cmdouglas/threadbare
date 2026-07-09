"""Checkpointed channel backfill. Orchestration is written against two small
Protocols (HistoryFetcher, BackfillSink) rather than discord.py/psycopg
directly, so the paging/checkpoint/resume logic is unit-testable with
in-memory fakes — no live gateway connection or real database required.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol

import discord

from threadbare.sync_worker import repository, transform
from threadbare.sync_worker.checkpoints import advance_backfill_progress
from threadbare.sync_worker.discord_types import MessageLike

DEFAULT_BATCH_SIZE = 100
ATTACHMENT_URL_TTL = timedelta(hours=24)


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
        after = checkpoint

        if progress.complete:
            break

    return total_written
