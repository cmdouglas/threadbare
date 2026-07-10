import asyncio
import os
import uuid

import aiohttp
import discord
import pytest

from threadbare.db.pool import create_pool
from threadbare.sync_worker.bot import ThreadbareClient

pytestmark = pytest.mark.live_discord

POLL_INTERVAL_SECONDS = 0.2
POLL_TIMEOUT_SECONDS = 15.0


def _require_thread_id() -> int | None:
    thread_id = os.environ.get("DISCORD_TEST_THREAD_ID")
    return int(thread_id) if thread_id else None


async def _poll_until(condition, *, description: str) -> None:
    elapsed = 0.0
    while elapsed < POLL_TIMEOUT_SECONDS:
        if await condition():
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
    pytest.fail(f"{description} did not happen within {POLL_TIMEOUT_SECONDS}s")


async def _wait_for_client_ready(client) -> None:
    async def _setup_started() -> bool:
        return client._ready is not discord.utils.MISSING

    await _poll_until(_setup_started, description="client's internal setup starting")
    await asyncio.wait_for(client.wait_until_ready(), timeout=15)

    async def _channel_discovered() -> bool:
        async with client.pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT count(*) AS n FROM channels")
            return (await cur.fetchone())["n"] > 0

    await _poll_until(_channel_discovered, description="channel discovery completing")


async def _cleanup(pool) -> None:
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM messages")
        await conn.execute("DELETE FROM thread_sync_state")
        await conn.execute("DELETE FROM threads")
        await conn.execute("DELETE FROM sync_state")
        await conn.execute("DELETE FROM channels")
        await conn.execute("DELETE FROM guilds")
        await conn.execute("DELETE FROM users")
        await conn.execute("DELETE FROM worker_heartbeat")


async def test_full_lifecycle_create_edit_delete_in_a_thread_via_webhook():
    """Regression test for the live FK-crash bug this session fixed: a real
    Discord message posted into a thread must not raise ForeignKeyViolation
    on messages.thread_id — the sync worker must upsert the threads row
    itself, on the fly, exactly like discover_channels() does for channels.

    Posts into a persistent, manually-created test thread (see
    DEVELOPMENT.md) rather than creating one on the fly: Discord webhooks
    can only auto-create threads in forum channels, not plain text channels.
    """
    webhook_url = os.environ.get("DISCORD_TEST_WEBHOOK_URL")
    thread_id = _require_thread_id()
    if not webhook_url or not thread_id:
        pytest.skip("DISCORD_TEST_WEBHOOK_URL/DISCORD_TEST_THREAD_ID not set; see DEVELOPMENT.md")

    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])
    token = os.environ["DISCORD_BOT_TOKEN"]
    test_database_url = os.environ["TEST_DATABASE_URL"]

    pool = create_pool(test_database_url)
    await pool.open()
    client = ThreadbareClient(guild_id=guild_id, pool=pool)
    start_task = asyncio.create_task(client.start(token))

    async def _message_row(message_id: int) -> dict | None:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT channel_id, thread_id, content FROM messages WHERE id = %s", (message_id,)
            )
            return await cur.fetchone()

    try:
        await _wait_for_client_ready(client)

        webhook = discord.Webhook.from_url(webhook_url, client=client)
        marker = uuid.uuid4().hex[:8]
        created = await webhook.send(
            content=f"threadbare thread lifecycle test {marker}: created",
            thread=discord.Object(thread_id),
            wait=True,
        )
        message_id = created.id

        async def _thread_row_exists() -> bool:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("SELECT id FROM threads WHERE id = %s", (thread_id,))
                return await cur.fetchone() is not None

        await _poll_until(_thread_row_exists, description="thread row appearing in Postgres")

        async def _created_row() -> bool:
            row = await _message_row(message_id)
            return (
                row is not None
                and row["thread_id"] == thread_id
                and row["channel_id"] is None
                and row["content"] == f"threadbare thread lifecycle test {marker}: created"
            )

        await _poll_until(_created_row, description="created thread message appearing in Postgres")

        await webhook.edit_message(
            message_id,
            content=f"threadbare thread lifecycle test {marker}: edited",
            thread=discord.Object(thread_id),
        )

        async def _edited_row() -> bool:
            row = await _message_row(message_id)
            return (
                row is not None
                and row["content"] == f"threadbare thread lifecycle test {marker}: edited"
            )

        await _poll_until(_edited_row, description="edited thread message content updating")

        await webhook.delete_message(message_id, thread=discord.Object(thread_id))

        async def _row_is_gone() -> bool:
            return await _message_row(message_id) is None

        await _poll_until(_row_is_gone, description="deleted thread message disappearing")
    finally:
        await client.close()
        start_task.cancel()
        await _cleanup(pool)
        await pool.close()


async def test_discover_and_backfill_guild_picks_up_a_pre_existing_thread():
    """The thread-specific equivalent of
    test_discover_and_backfill_guild_populates_data_without_preseeding: a
    thread message that existed before the sync worker ever started must be
    discovered and backfilled from an empty database, with no hand-seeded
    threads/thread_sync_state row.
    """
    webhook_url = os.environ.get("DISCORD_TEST_WEBHOOK_URL")
    thread_id = _require_thread_id()
    if not webhook_url or not thread_id:
        pytest.skip("DISCORD_TEST_WEBHOOK_URL/DISCORD_TEST_THREAD_ID not set; see DEVELOPMENT.md")

    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])
    token = os.environ["DISCORD_BOT_TOKEN"]
    test_database_url = os.environ["TEST_DATABASE_URL"]

    pool = create_pool(test_database_url)
    await pool.open()
    await _cleanup(pool)

    # Post into the persistent test thread out of band, before the sync
    # worker's client even starts — a bare aiohttp session is enough for a
    # one-shot webhook POST, no ThreadbareClient/gateway connection needed.
    marker = uuid.uuid4().hex[:8]
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(webhook_url, session=session)
        await webhook.send(
            content=f"threadbare thread backfill test {marker}: pre-existing message",
            thread=discord.Object(thread_id),
            wait=True,
        )

    client = ThreadbareClient(guild_id=guild_id, pool=pool)
    start_task = asyncio.create_task(client.start(token))

    try:
        # backfill_guild now also walks active/archived thread discovery and
        # thread backfill (extra REST round-trips beyond plain channel
        # backfill), so this needs more headroom than the 10s the
        # channel-only version of this wait used elsewhere.
        for _ in range(300):
            if client._backfill_task is not None and client._backfill_task.done():
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("backfill_guild did not complete within 30s")

        async def _thread_backfilled() -> bool:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("SELECT id FROM threads WHERE id = %s", (thread_id,))
                if await cur.fetchone() is None:
                    return False
                await cur.execute(
                    "SELECT count(*) AS n FROM messages WHERE thread_id = %s AND content = %s",
                    (thread_id, f"threadbare thread backfill test {marker}: pre-existing message"),
                )
                return (await cur.fetchone())["n"] == 1

        await _poll_until(_thread_backfilled, description="pre-existing thread message backfilling")
    finally:
        await client.close()
        start_task.cancel()
        await _cleanup(pool)
        await pool.close()
