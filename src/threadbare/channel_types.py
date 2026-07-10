"""Local constants mirroring discord.py's ChannelType values (confirmed
against the installed library, not guessed), so the web app never needs to
depend on discord.py itself — that stays a sync-worker-only dependency,
matching sync_worker/discord_types.py's existing "discord.py-free elsewhere"
convention. channels.type stores the raw integer with no local enum table.
"""

TEXT = 0
CATEGORY = 4
NEWS = 5
FORUM = 15
MEDIA = 16

# Forum/media channels have no direct messages of their own — every post is
# a thread (ROADMAP.md §1's forum-channel work; backfill.py's
# SKIPPED_CHANNEL_TYPES already excludes these from direct history backfill
# for the same reason).
FORUM_LIKE_TYPES = frozenset({FORUM, MEDIA})

# Freeform channels can hold both direct messages and native Discord threads.
FREEFORM_TYPES = frozenset({TEXT, NEWS})
