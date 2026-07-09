import asyncio
import os

import pytest

from threadbare.db.pool import create_pool
from threadbare.sync_worker.bot import ThreadbareClient

pytestmark = pytest.mark.live_discord


async def test_discover_and_backfill_guild_populates_data_without_preseeding():
    """The payoff test for the channel-discovery + backfill orchestrator:
    starting from a completely empty database (no hand-seeded channel row,
    unlike every earlier manual verification), the real worker discovers
    the guild's channels and backfills their content automatically.
    """
    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])
    token = os.environ["DISCORD_BOT_TOKEN"]
    test_database_url = os.environ["TEST_DATABASE_URL"]

    pool = create_pool(test_database_url)
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM messages")
        await conn.execute("DELETE FROM sync_state")
        await conn.execute("DELETE FROM channels")
        await conn.execute("DELETE FROM guilds")
        await conn.execute("DELETE FROM users")
        await conn.execute("DELETE FROM worker_heartbeat")

    client = ThreadbareClient(guild_id=guild_id, pool=pool)
    start_task = asyncio.create_task(client.start(token))

    try:
        for _ in range(100):
            if client._backfill_task is not None and client._backfill_task.done():
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("backfill_guild did not complete within 10s")

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) AS n FROM channels")
                assert (await cur.fetchone())["n"] > 0
                await cur.execute("SELECT count(*) AS n FROM messages")
                assert (await cur.fetchone())["n"] > 0
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
