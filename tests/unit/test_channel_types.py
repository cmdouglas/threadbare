from threadbare.channel_types import (
    CATEGORY,
    FORUM,
    FORUM_LIKE_TYPES,
    MEDIA,
    NEWS,
    NON_CONTENT_TYPES,
    STAGE_VOICE,
    TEXT,
    VOICE,
)


def test_values_match_discords_own_channel_type_enum():
    # Confirmed against the installed discord.py's discord.ChannelType.
    assert TEXT == 0
    assert VOICE == 2
    assert CATEGORY == 4
    assert NEWS == 5
    assert STAGE_VOICE == 13
    assert FORUM == 15
    assert MEDIA == 16


def test_non_content_types_contains_category_voice_and_stage_voice():
    assert NON_CONTENT_TYPES == {CATEGORY, VOICE, STAGE_VOICE}


def test_forum_like_types_contains_forum_and_media():
    assert FORUM_LIKE_TYPES == {FORUM, MEDIA}


def test_forum_like_types_excludes_the_freeform_text_and_news_types():
    """board_tree.board_view_mode classifies by "is it forum-like", falling
    through to freeform for everything else -- so text/news must not be in
    FORUM_LIKE_TYPES. (There is deliberately no FREEFORM_TYPES constant: it
    existed but nothing ever branched on it.)
    """
    assert TEXT not in FORUM_LIKE_TYPES
    assert NEWS not in FORUM_LIKE_TYPES
