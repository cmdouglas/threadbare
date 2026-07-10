import asyncio
import os

import discord
import pytest

from threadbare.db.pool import create_pool
from threadbare.sync_worker.bot import ThreadbareClient

pytestmark = pytest.mark.live_discord

POLL_INTERVAL_SECONDS = 0.2
POLL_TIMEOUT_SECONDS = 15.0


async def _poll_until(condition, *, description: str) -> None:
    """Polls `condition` (an async callable returning bool) rather than
    sleeping a fixed amount — gateway delivery isn't instant, and a fixed
    sleep would either be flaky (too short) or slow the suite down needlessly
    (too long).
    """
    elapsed = 0.0
    while elapsed < POLL_TIMEOUT_SECONDS:
        if await condition():
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
    pytest.fail(f"{description} did not happen within {POLL_TIMEOUT_SECONDS}s")


async def test_full_lifecycle_create_edit_delete_via_webhook():
    """Exercises the sync worker's live gateway path end to end for the
    first time: a real Discord-originated create, edit, and delete. Every
    earlier live test either verified handlers via fake payloads against
    real Postgres, or verified reads only (backfill/reconciliation) — never
    a real gateway-delivered write.

    Uses a Discord webhook as the posting actor rather than a second bot, so
    the sync-worker bot's own permissions never need write access. See
    DEVELOPMENT.md's test-bot-setup section for how to create one and why.
    """
    webhook_url = os.environ.get("DISCORD_TEST_WEBHOOK_URL")
    if not webhook_url:
        pytest.skip("DISCORD_TEST_WEBHOOK_URL is not set; see DEVELOPMENT.md")

    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])
    token = os.environ["DISCORD_BOT_TOKEN"]
    test_database_url = os.environ["TEST_DATABASE_URL"]

    pool = create_pool(test_database_url)
    await pool.open()
    client = ThreadbareClient(guild_id=guild_id, pool=pool)
    start_task = asyncio.create_task(client.start(token))

    async def _message_row(message_id: int) -> dict | None:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT content FROM messages WHERE id = %s", (message_id,))
            return await cur.fetchone()

    try:
        # client.start() has just been scheduled, not necessarily run yet, so
        # wait_until_ready() would race and raise if called before discord.py's
        # _async_setup_hook has created the internal ready event.
        async def _setup_started() -> bool:
            return client._ready is not discord.utils.MISSING

        await _poll_until(_setup_started, description="client's internal setup starting")
        await asyncio.wait_for(client.wait_until_ready(), timeout=15)

        # wait_until_ready() resolves as soon as discord.py's own internal
        # READY handling completes, which races ahead of ThreadbareClient's
        # on_ready() — the discover_channels() call it awaits hasn't
        # necessarily finished, so channel rows (needed for messages' FK)
        # may not exist yet.
        async def _channel_discovered() -> bool:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("SELECT count(*) AS n FROM channels")
                return (await cur.fetchone())["n"] > 0

        await _poll_until(_channel_discovered, description="channel discovery completing")

        webhook = discord.Webhook.from_url(webhook_url, client=client)

        created = await webhook.send(content="threadbare live test: created", wait=True)
        message_id = created.id

        await _poll_until(
            lambda: _row_has_content(_message_row, message_id, "threadbare live test: created"),
            description="created message appearing in Postgres",
        )

        await webhook.edit_message(message_id, content="threadbare live test: edited")

        await _poll_until(
            lambda: _row_has_content(_message_row, message_id, "threadbare live test: edited"),
            description="edited content appearing in Postgres",
        )

        await webhook.delete_message(message_id)

        async def _row_is_gone() -> bool:
            return await _message_row(message_id) is None

        await _poll_until(_row_is_gone, description="deleted message disappearing from Postgres")
    finally:
        await client.close()
        start_task.cancel()
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM messages")
            await conn.execute("DELETE FROM sync_state")
            await conn.execute("DELETE FROM channels")
            await conn.execute("DELETE FROM guilds")
            await conn.execute("DELETE FROM users")
            await conn.execute("DELETE FROM worker_heartbeat")
        await pool.close()


async def _row_has_content(fetch_row, message_id: int, expected_content: str) -> bool:
    row = await fetch_row(message_id)
    return row is not None and row["content"] == expected_content
