import asyncio
import os
import uuid

import discord
import pytest

from threadbare.db.pool import create_pool
from threadbare.sync_worker.bot import ThreadbareClient

pytestmark = pytest.mark.live_discord

POLL_INTERVAL_SECONDS = 0.2
POLL_TIMEOUT_SECONDS = 15.0


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


async def test_discover_channels_computes_is_public_for_the_real_forum_channel():
    forum_channel_id = os.environ.get("DISCORD_TEST_FORUM_CHANNEL_ID")
    if not forum_channel_id:
        pytest.skip("DISCORD_TEST_FORUM_CHANNEL_ID is not set; see DEVELOPMENT.md")
    forum_channel_id = int(forum_channel_id)

    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])
    token = os.environ["DISCORD_BOT_TOKEN"]
    test_database_url = os.environ["TEST_DATABASE_URL"]

    pool = create_pool(test_database_url)
    await pool.open()
    await _cleanup(pool)
    client = ThreadbareClient(guild_id=guild_id, pool=pool)
    start_task = asyncio.create_task(client.start(token))

    try:
        await _wait_for_client_ready(client)

        async def _forum_channel_discovered() -> bool:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT is_public FROM channels WHERE id = %s", (forum_channel_id,)
                )
                row = await cur.fetchone()
                return row is not None

        await _poll_until(_forum_channel_discovered, description="forum channel discovery")

        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT is_public FROM channels WHERE id = %s", (forum_channel_id,))
            row = await cur.fetchone()
        assert row["is_public"] is True

        # Directly validates the TypeError risk this session's forum-channel
        # work found: ForumChannel.archived_threads() has no private= kwarg,
        # unlike TextChannel's — a fake can drift from the real signature,
        # but a live call against the real object can't.
        forum_channel = client.get_channel(forum_channel_id) or await client.fetch_channel(
            forum_channel_id
        )
        assert isinstance(forum_channel, discord.ForumChannel)
        async for _ in forum_channel.archived_threads():
            pass
    finally:
        await client.close()
        start_task.cancel()
        await _cleanup(pool)
        await pool.close()


async def test_webhook_created_forum_post_is_discovered_live_via_on_thread_create():
    """A webhook posting with thread_name= into a forum channel genuinely
    fires THREAD_CREATE (unlike a plain text channel, which 400s) — this
    exercises on_thread_create live for the first time, independent of
    backfill/reconciliation.
    """
    forum_channel_id = os.environ.get("DISCORD_TEST_FORUM_CHANNEL_ID")
    forum_webhook_url = os.environ.get("DISCORD_TEST_FORUM_WEBHOOK_URL")
    if not forum_channel_id or not forum_webhook_url:
        pytest.skip(
            "DISCORD_TEST_FORUM_CHANNEL_ID/DISCORD_TEST_FORUM_WEBHOOK_URL not set; "
            "see DEVELOPMENT.md"
        )

    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])
    token = os.environ["DISCORD_BOT_TOKEN"]
    test_database_url = os.environ["TEST_DATABASE_URL"]

    pool = create_pool(test_database_url)
    await pool.open()
    await _cleanup(pool)
    client = ThreadbareClient(guild_id=guild_id, pool=pool)
    start_task = asyncio.create_task(client.start(token))

    try:
        await _wait_for_client_ready(client)

        async def _forum_channel_discovered() -> bool:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) AS n FROM channels WHERE id = %s", (int(forum_channel_id),)
                )
                return (await cur.fetchone())["n"] > 0

        await _poll_until(_forum_channel_discovered, description="forum channel discovery")

        webhook = discord.Webhook.from_url(forum_webhook_url, client=client)
        post_name = f"threadbare-live-forum-test-{uuid.uuid4().hex[:8]}"
        created = await webhook.send(
            content="threadbare forum-channel test: created", thread_name=post_name, wait=True
        )
        thread_id = created.channel.id

        async def _thread_row_exists() -> bool:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("SELECT id FROM threads WHERE id = %s", (thread_id,))
                return await cur.fetchone() is not None

        await _poll_until(_thread_row_exists, description="forum post appearing in Postgres")

        async def _message_row_exists() -> bool:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM messages WHERE id = %s AND thread_id = %s",
                    (created.id, thread_id),
                )
                return await cur.fetchone() is not None

        await _poll_until(_message_row_exists, description="forum post's message appearing")
    finally:
        await client.close()
        start_task.cancel()
        await _cleanup(pool)
        await pool.close()
