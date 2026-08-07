import discord

from threadbare.sync_worker.channel_type_sets import (
    NO_CONTENT_ROW,
    NO_ROW,
    SKIPPED_FOR_DIRECT_HISTORY,
)


def test_no_row_contains_voice_and_stage_voice():
    assert set(NO_ROW) == {discord.ChannelType.voice, discord.ChannelType.stage_voice}


def test_no_content_row_adds_category_to_no_row():
    assert set(NO_CONTENT_ROW) == set(NO_ROW) | {discord.ChannelType.category}


def test_skipped_for_direct_history_adds_forum_and_media_to_no_content_row():
    # discord.py's ForumChannel class backs both channel.type values (a media
    # channel is a ForumChannel with type=media, confirmed against the
    # installed discord.py source) -- neither has a .history() method, since
    # every post is a thread rather than a top-level message. Resyncing a
    # media channel the same way as a forum channel hits the identical
    # AttributeError if media is missing here.
    assert set(SKIPPED_FOR_DIRECT_HISTORY) == set(NO_CONTENT_ROW) | {
        discord.ChannelType.forum,
        discord.ChannelType.media,
    }


def test_skipped_for_direct_history_excludes_ordinary_text_channels():
    assert discord.ChannelType.text not in SKIPPED_FOR_DIRECT_HISTORY
