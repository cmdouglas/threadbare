import os

import pytest

from threadbare.sync_worker.backfill import DiscordHistoryFetcher
from threadbare.sync_worker.bot import ThreadbareClient

pytestmark = pytest.mark.live_discord


async def test_real_fetcher_returns_nonempty_content_from_general():
    guild_id = int(os.environ["DISCORD_TEST_GUILD_ID"])
    token = os.environ["DISCORD_BOT_TOKEN"]

    client = ThreadbareClient(guild_id=guild_id)
    result: dict[str, object] = {}

    @client.event
    async def on_ready():
        try:
            guild = await client.fetch_guild(guild_id)
            channels = await guild.fetch_channels()
            general = next(c for c in channels if c.name == "general")

            fetcher = DiscordHistoryFetcher(client)
            result["messages"] = await fetcher.fetch_batch(
                channel_id=general.id, after=None, limit=10
            )
        finally:
            await client.close()

    await client.start(token)

    messages = result["messages"]
    assert messages
    assert any(m.content for m in messages)
    assert any(m.author.display_name for m in messages)
