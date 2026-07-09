import discord


class ThreadbareClient(discord.Client):
    """Thin glue only: unpacks discord.py objects and delegates to plain,
    dependency-injected functions elsewhere in this package. No business
    logic belongs in this class or in events.py — see DEVELOPMENT.md /
    the sync worker plan for why (testability without a live gateway).
    """

    def __init__(self, *, guild_id: int, **kwargs):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, **kwargs)
        self.guild_id = guild_id
