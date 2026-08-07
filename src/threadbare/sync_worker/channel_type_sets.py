"""The sync worker's channel-type predicates, in discord.ChannelType terms.

These lived in three separate places (backfill.SKIPPED_CHANNEL_TYPES, a
function-local tuple inside discovery.discover_channels, and
events._NO_ROW_CHANNEL_TYPES) which made them look like three copies of one
idea. They are not: there are three genuinely different questions, and having
them side by side is the only way to see how they differ.

    NO_ROW              ⊂ NO_CONTENT_ROW ⊂ SKIPPED_FOR_DIRECT_HISTORY
    voice, stage_voice    + category       + forum, media

The int-valued equivalent of NO_CONTENT_ROW is threadbare.channel_types
.NON_CONTENT_TYPES, which the web app and db layer use -- those read
channels.type straight out of Postgres and must stay discord.py-free (see
channel_types.py's own docstring), so the duplication across the two
representations is deliberate, unlike the duplication this module removes.
"""

import discord

# Get no `channels` row at all. Voice/stage-voice channels are a stated
# non-goal (DESIGN.md §2) and, unlike categories, nothing ever parents off
# them, so there is no FK reason to store them either.
NO_ROW = (discord.ChannelType.voice, discord.ChannelType.stage_voice)

# Never a browsable board. Adds categories, which *do* need a row -- a child
# channel's parent_id is an FK into channels itself -- but hold no content of
# their own.
NO_CONTENT_ROW = (discord.ChannelType.category, *NO_ROW)

# Never walked for top-level message history. Adds forum and media channels:
# they have no direct messages by construction (every post is a thread,
# backfilled separately), so asking Discord for their history is meaningless.
# discord.py backs both channel.type values with the same ForumChannel class
# (confirmed against the installed discord.py source), and neither has a
# .history() method -- both belong here for that same reason.
SKIPPED_FOR_DIRECT_HISTORY = (
    discord.ChannelType.forum,
    discord.ChannelType.media,
    *NO_CONTENT_ROW,
)
