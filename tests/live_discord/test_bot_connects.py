import os

import pytest

from threadbare.sync_worker.bot import ThreadbareClient

pytestmark = pytest.mark.live_discord


async def test_bot_connects_and_fetches_guild():
    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])
    token = os.environ["DISCORD_BOT_TOKEN"]

    client = ThreadbareClient(guild_id=guild_id)
    result: dict[str, object] = {}

    @client.event
    async def on_ready():
        try:
            guild = await client.fetch_guild(guild_id)
            result["guild_id"] = guild.id
            result["guild_name"] = guild.name
        finally:
            await client.close()

    await client.start(token)

    assert result["guild_id"] == guild_id
    assert result["guild_name"]
