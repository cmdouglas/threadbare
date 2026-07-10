from datetime import UTC, datetime, timedelta

import pytest

from .conftest import E2E_GUILD_ID

CHANNEL_ID = 900001
THREAD_ID = 900010
USER_ID = 900002
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
    assert "31" in row.locator(".board-post-count").inner_text()


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


def test_search_click_through_lands_on_the_right_post(page, live_server, seeded):
    page.goto(f"{live_server}/search?q=pizza")
    assert "1 result" in page.content()

    page.click(".search-result a")

    assert page.url.endswith(f"/board/{CHANNEL_ID}/continuous/page/1#post-900200")
    assert page.locator("#post-900200").is_visible()
    assert "pizza recipe" in page.locator("#post-900200").inner_text()


def test_css_custom_property_contract_is_present(page, live_server, seeded):
    page.goto(f"{live_server}/")

    color_bg = page.evaluate(
        "getComputedStyle(document.body).getPropertyValue('--color-bg')"
    )
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
