from datetime import UTC, datetime, timedelta

import pytest

from .conftest import E2E_GUILD_ID

CHANNEL_ID = 900001
THREAD_ID = 900010
USER_ID = 900002
ATTACHMENT_ID = 900300
EMBED_MESSAGE_ID = 900201
LINK_EMBED_MESSAGE_ID = 900202
SYSTEM_MESSAGE_ID = 900203
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _seed(conn):
    conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (E2E_GUILD_ID, "E2E Guild"),
    )
    conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, 0, 'general', true, true) ON CONFLICT DO NOTHING
        """,
        (CHANNEL_ID, E2E_GUILD_ID),
    )
    conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, now())",
        (THREAD_ID, CHANNEL_ID, "e2e topic"),
    )
    conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (USER_ID, "alice"),
    )
    for i in range(30):
        conn.execute(
            """
            INSERT INTO messages (id, thread_id, author_id, content, posted_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (900100 + i, THREAD_ID, USER_ID, f"topic message {i}", BASE + timedelta(seconds=i)),
        )
    conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (900200, CHANNEL_ID, USER_ID, "a pizza recipe for testing search"),
    )
    conn.execute(
        """
        INSERT INTO attachments
            (id, message_id, filename, content_type, size, cached_url, url_expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            ATTACHMENT_ID,
            900200,
            "cat.png",
            "image/png",
            100,
            "https://cdn.example/cat.png",
            BASE + timedelta(days=1),
        ),
    )
    conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (EMBED_MESSAGE_ID, CHANNEL_ID, USER_ID, "a link with a big embed image"),
    )
    conn.execute(
        """
        INSERT INTO embeds (message_id, position, image_url)
        VALUES (%s, %s, %s)
        """,
        (EMBED_MESSAGE_ID, 0, "https://cdn.example/wide-screenshot.png"),
    )
    conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (LINK_EMBED_MESSAGE_ID, CHANNEL_ID, USER_ID, "https://example.com/vox-article"),
    )
    conn.execute(
        """
        INSERT INTO embeds (message_id, position, type, thumbnail_url)
        VALUES (%s, %s, %s, %s)
        """,
        (LINK_EMBED_MESSAGE_ID, 0, "link", "https://cdn.example/vox-thumb.png"),
    )
    conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at, type)
        VALUES (%s, %s, %s, %s, now(), %s)
        """,
        (SYSTEM_MESSAGE_ID, CHANNEL_ID, USER_ID, "", 7),
    )
    conn.commit()


def _cleanup(conn):
    conn.execute("DELETE FROM messages WHERE channel_id = %s", (CHANNEL_ID,))
    conn.execute("DELETE FROM threads WHERE id = %s", (THREAD_ID,))
    conn.execute("DELETE FROM channels WHERE id = %s", (CHANNEL_ID,))
    conn.execute("DELETE FROM guilds WHERE id = %s", (E2E_GUILD_ID,))
    conn.execute("DELETE FROM users WHERE id = %s", (USER_ID,))
    conn.commit()


@pytest.fixture
def seeded(seed_conn):
    _seed(seed_conn)
    yield
    _cleanup(seed_conn)


def test_board_index_shows_seeded_board_and_post_count(page, live_server, seeded):
    page.goto(f"{live_server}/")

    row = page.locator(".board-row", has_text="general")
    assert row.count() == 1
    assert "34" in row.locator(".board-post-count").inner_text()


def test_topic_pagination_and_permalink_round_trip(page, live_server, seeded):
    page.goto(f"{live_server}/topic/{THREAD_ID}/page/1")
    assert page.locator("article.post").count() == 25
    assert "topic message 0" in page.content()

    page.goto(f"{live_server}/topic/{THREAD_ID}/page/2")
    assert page.locator("article.post").count() == 5
    assert "topic message 25" in page.content()

    permalink_href = page.locator("#post-900125 .post-permalink").get_attribute("href")
    assert permalink_href == f"/topic/{THREAD_ID}/page/2#post-900125"

    page.goto(f"{live_server}{permalink_href}")
    assert page.url.endswith("#post-900125")
    assert page.locator("#post-900125").is_visible()


def test_posts_per_page_switcher_changes_how_many_posts_render(page, live_server, seeded):
    page.goto(f"{live_server}/topic/{THREAD_ID}/page/1")
    assert page.locator("article.post").count() == 25

    page.locator(".posts-per-page-switcher a", has_text="50").click()

    assert "posts_per_page=50" in page.url
    assert page.locator("article.post").count() == 30


def test_jump_to_page_form_navigates_to_the_typed_page(page, live_server, seeded):
    page.goto(f"{live_server}/topic/{THREAD_ID}/page/1")

    page.locator(".jump-to-page input[name=page]").first.fill("2")
    page.locator(".jump-to-page button[type=submit]").first.click()

    assert page.url.endswith(f"/topic/{THREAD_ID}/page/2")
    assert "topic message 25" in page.content()


def test_topic_list_shows_per_topic_pagination_control(page, live_server, seeded):
    page.goto(f"{live_server}/board/{CHANNEL_ID}/topics")

    pagination = page.locator("tr.topic-pagination-row .pagination")
    assert pagination.count() == 1
    assert pagination.locator(".pagination-page").count() == 2

    pagination.locator(".pagination-page", has_text="2").click()

    assert page.url.endswith(f"/topic/{THREAD_ID}/page/2")
    assert "topic message 25" in page.content()


FORUM_CHANNEL_ID = 900600


def _seed_forum_board_with_many_topics(conn):
    conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (E2E_GUILD_ID, "E2E Guild"),
    )
    conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, 15, 'a forum', true, true) ON CONFLICT DO NOTHING
        """,
        (FORUM_CHANNEL_ID, E2E_GUILD_ID),
    )
    for i in range(26):
        conn.execute(
            "INSERT INTO threads (id, parent_channel_id, name, created_at) "
            "VALUES (%s, %s, %s, now())",
            (900610 + i, FORUM_CHANNEL_ID, f"forum topic {i}"),
        )
    conn.commit()


def _cleanup_forum_board_with_many_topics(conn):
    conn.execute("DELETE FROM threads WHERE parent_channel_id = %s", (FORUM_CHANNEL_ID,))
    conn.execute("DELETE FROM channels WHERE id = %s", (FORUM_CHANNEL_ID,))
    conn.commit()


@pytest.fixture
def seeded_forum_board(seed_conn):
    _seed_forum_board_with_many_topics(seed_conn)
    yield
    _cleanup_forum_board_with_many_topics(seed_conn)


def test_board_index_shows_pagination_control_for_a_multi_page_forum_board(
    page, live_server, seeded_forum_board
):
    page.goto(f"{live_server}/")

    row = page.locator(".board-row", has_text="a forum")
    assert row.count() == 1

    pagination = page.locator("tr.board-pagination-row .pagination")
    assert pagination.count() == 1
    assert pagination.locator(".pagination-page").count() == 2

    pagination.locator(".pagination-page", has_text="2").click()

    assert page.url.endswith(f"/board/{FORUM_CHANNEL_ID}/topics?page=2")
    # threads are listed newest-first (created_at DESC, id DESC); with all 26
    # sharing one created_at (single tx), the oldest (lowest id, "topic 0")
    # is the one left over on page 2.
    assert "forum topic 0" in page.content()


def test_search_click_through_lands_on_the_right_post(page, live_server, seeded):
    page.goto(f"{live_server}/search?q=pizza")
    assert "1 result" in page.content()

    page.click(".search-result a")

    assert page.url.endswith(f"/board/{CHANNEL_ID}/continuous/page/1#post-900200")
    assert page.locator("#post-900200").is_visible()
    assert "pizza recipe" in page.locator("#post-900200").inner_text()


def test_attachment_image_is_capped_to_the_viewport_height(page, live_server, seeded):
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1")

    max_height = page.locator(".attachment-image img").evaluate(
        "el => getComputedStyle(el).maxHeight"
    )
    assert max_height != "none"


def test_embed_image_does_not_overflow_the_post_box(page, live_server, seeded):
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1")

    post_box = page.locator(f"#post-{EMBED_MESSAGE_ID}")
    image = post_box.locator(".embed-image")
    assert image.count() == 1

    post_right_edge = post_box.evaluate("el => el.getBoundingClientRect().right")
    image_right_edge = image.evaluate("el => el.getBoundingClientRect().right")
    assert image_right_edge <= post_right_edge + 1


def test_link_unfurl_thumbnail_renders_large_not_small_and_floated(page, live_server, seeded):
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1")

    post_box = page.locator(f"#post-{LINK_EMBED_MESSAGE_ID}")
    image = post_box.locator(".embed-image")
    assert image.count() == 1
    assert post_box.locator(".embed-thumbnail").count() == 0

    post_right_edge = post_box.evaluate("el => el.getBoundingClientRect().right")
    image_right_edge = image.evaluate("el => el.getBoundingClientRect().right")
    assert image_right_edge <= post_right_edge + 1


def test_system_message_renders_real_text_not_a_blank_post(page, live_server, seeded):
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1")

    content = page.locator(f"#post-{SYSTEM_MESSAGE_ID} .post-content")
    assert content.get_attribute("class") == "post-content post-content-system"
    assert content.inner_text().strip() != ""
    assert "alice" in content.inner_text()


def test_avatar_toggle_round_trip(page, live_server, seeded):
    page.goto(f"{live_server}/topic/{THREAD_ID}/page/1")
    assert page.locator(".post-avatar").count() > 0

    page.click(".avatar-toggle a")

    assert page.locator(".post-avatar").count() == 0
    assert "avatars=off" in page.url

    page.goto(f"{live_server}/topic/{THREAD_ID}/page/1")
    assert page.locator(".post-avatar").count() == 0


def test_css_custom_property_contract_is_present(page, live_server, seeded):
    page.goto(f"{live_server}/")

    color_bg = page.evaluate("getComputedStyle(document.body).getPropertyValue('--color-bg')")
    assert color_bg.strip() != ""


def test_theme_switch_via_query_param_changes_computed_stylesheet(page, live_server, seeded):
    page.goto(f"{live_server}/?theme=plain")
    plain_href = page.locator("link[rel=stylesheet]").get_attribute("href")
    plain_color_bg = page.evaluate(
        "getComputedStyle(document.body).getPropertyValue('--color-bg')"
    ).strip()
    assert "theme-plain.css" in plain_href

    page.goto(f"{live_server}/?theme=subsilver")
    subsilver_href = page.locator("link[rel=stylesheet]").get_attribute("href")
    subsilver_color_bg = page.evaluate(
        "getComputedStyle(document.body).getPropertyValue('--color-bg')"
    ).strip()
    assert "theme-subsilver.css" in subsilver_href

    assert plain_color_bg != subsilver_color_bg


def test_theme_choice_persists_across_navigation_without_query_param(page, live_server, seeded):
    page.goto(f"{live_server}/?theme=plain")
    assert "theme-plain.css" in page.locator("link[rel=stylesheet]").get_attribute("href")

    page.goto(f"{live_server}/")

    assert "theme-plain.css" in page.locator("link[rel=stylesheet]").get_attribute("href")


def test_vbulletin_dark_theme_is_reachable_via_query_param(page, live_server, seeded):
    page.goto(f"{live_server}/?theme=vbulletin-dark")

    href = page.locator("link[rel=stylesheet]").get_attribute("href")
    assert "theme-vbulletin-dark.css" in href

    response = page.request.get(f"{live_server}{href}")
    assert response.status == 200


def test_terminal_theme_is_reachable_via_query_param(page, live_server, seeded):
    page.goto(f"{live_server}/?theme=terminal")

    href = page.locator("link[rel=stylesheet]").get_attribute("href")
    assert "theme-terminal.css" in href

    response = page.request.get(f"{live_server}{href}")
    assert response.status == 200
