import os

import pytest

from threadbare.sync_worker.bot import ThreadbareClient
from threadbare.sync_worker.permissions import compute_is_public, everyone_overwrite

pytestmark = pytest.mark.live_discord


async def test_real_general_channel_overwrites_resolve_to_public():
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

            category_overwrite = everyone_overwrite(general.category) if general.category else None
            result["is_public"] = compute_is_public(
                guild.default_role.permissions.value,
                category_overwrite,
                everyone_overwrite(general),
            )
        finally:
            await client.close()

    await client.start(token)

    # #general is @everyone-readable in our test server by construction —
    # this proves the discord_types.py Protocols / everyone_overwrite
    # extraction actually match discord.py's real object shapes, which the
    # fake-object integration tests in test_events.py can't verify alone.
    assert result["is_public"] is True
