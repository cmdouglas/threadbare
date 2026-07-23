from datetime import UTC, datetime

from threadbare.rendering.system_messages import (
    is_system_message_type,
    render_system_message_text,
)


def test_is_system_message_type_false_for_default():
    assert is_system_message_type(0) is False


def test_is_system_message_type_false_for_reply():
    assert is_system_message_type(19) is False


def test_is_system_message_type_true_for_new_member():
    assert is_system_message_type(7) is True


def test_render_system_message_text_new_member_is_deterministic_by_timestamp():
    posted_at = datetime(2026, 1, 1, tzinfo=UTC)

    first = render_system_message_text(
        7, content="", author_display_name="alice", posted_at=posted_at
    )
    second = render_system_message_text(
        7, content="", author_display_name="alice", posted_at=posted_at
    )

    assert first == second
    assert "alice" in first


def test_render_system_message_text_pins_add():
    text = render_system_message_text(
        6, content="", author_display_name="alice", posted_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert text == "alice pinned a message to this channel."


def test_render_system_message_text_premium_guild_subscription_no_content():
    text = render_system_message_text(
        8, content="", author_display_name="alice", posted_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert text == "alice just boosted the server!"


def test_render_system_message_text_premium_guild_subscription_with_content():
    text = render_system_message_text(
        8, content="4", author_display_name="alice", posted_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert text == "alice just boosted the server **4** times!"


def test_render_system_message_text_tier_boost_includes_level():
    text = render_system_message_text(
        9, content="", author_display_name="alice", posted_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert "alice just boosted the server!" in text
    assert "**Level 1!**" in text


def test_render_system_message_text_channel_name_change():
    text = render_system_message_text(
        4,
        content="new-name",
        author_display_name="alice",
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert text == "alice changed the channel name: **new-name**"


def test_render_system_message_text_thread_created():
    text = render_system_message_text(
        18,
        content="a thread topic",
        author_display_name="alice",
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert text == "alice started a thread: **a thread topic**."


def test_render_system_message_text_unknown_type_falls_back_to_generic_notice():
    text = render_system_message_text(
        999, content="", author_display_name="alice", posted_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert text == "This is a system message."
