from threadbare import urls


def test_board_url():
    assert urls.board_url(10) == "/board/10"


def test_topic_url_defaults_to_page_one():
    assert urls.topic_url(500) == "/topic/500/page/1"


def test_topic_url_with_explicit_page():
    assert urls.topic_url(500, page=3) == "/topic/500/page/3"


def test_continuous_url():
    assert urls.continuous_url(10, page=2) == "/board/10/continuous/page/2"


def test_week_url():
    assert urls.week_url(10, "2026-W28", page=1) == "/board/10/week/2026-W28/page/1"


def test_user_url():
    assert urls.user_url(42) == "/user/42"


def test_attachment_proxy_url():
    assert urls.attachment_proxy_url(999) == "/att/999"


def test_permalink_for_message_in_a_thread():
    row = {"id": 100, "channel_id": None, "thread_id": 500}

    assert urls.permalink_for_message(row, page=2) == "/topic/500/page/2#post-100"


def test_permalink_for_message_in_a_freeform_channel_uses_continuous_view():
    row = {"id": 100, "channel_id": 10, "thread_id": None}

    assert urls.permalink_for_message(row, page=1) == "/board/10/continuous/page/1#post-100"


def test_discord_deep_link_url_for_a_thread_message():
    row = {"id": 100, "channel_id": None, "thread_id": 500}

    url = urls.discord_deep_link_url(guild_id=1, message_row=row)

    assert url == "https://discord.com/channels/1/500/100"


def test_discord_deep_link_url_for_a_channel_message():
    row = {"id": 100, "channel_id": 10, "thread_id": None}

    url = urls.discord_deep_link_url(guild_id=1, message_row=row)

    assert url == "https://discord.com/channels/1/10/100"
