from threadbare.channel_types import (
    CATEGORY,
    FORUM,
    FORUM_LIKE_TYPES,
    FREEFORM_TYPES,
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


def test_freeform_types_contains_text_and_news():
    assert FREEFORM_TYPES == {TEXT, NEWS}


def test_forum_like_and_freeform_types_are_disjoint():
    assert FORUM_LIKE_TYPES.isdisjoint(FREEFORM_TYPES)
