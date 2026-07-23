"""Proves the whole subpath-deployment chain together (ProxyFix -> request
.script_root -> templates/render_service -> rendered <a href>s) through a
real server and browser -- the one tier that exercises the full path rather
than any single layer in isolation. No real Caddy sits in front of
live_server here (see conftest.py's own precedent for what this tier does
and doesn't reproduce), so `X-Forwarded-Prefix` is set directly via
Playwright's extra HTTP headers to simulate what a subpath-configured Caddy
(docs/self-hosting.md's "Running at a subpath" section) sends on every
request.
"""

from datetime import UTC, datetime

from .conftest import E2E_GUILD_ID

CHANNEL_ID = 900301
THREAD_ID = 900310
USER_ID = 900302
BASE = datetime(2026, 1, 1, tzinfo=UTC)
PREFIX = "/discord-mirror"


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
        (THREAD_ID, CHANNEL_ID, "subpath topic"),
    )
    conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (USER_ID, "alice"),
    )
    conn.execute(
        """
        INSERT INTO messages (id, thread_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (900400, THREAD_ID, USER_ID, "original", BASE),
    )
    conn.execute(
        """
        INSERT INTO messages (id, thread_id, author_id, content, reply_to_id, posted_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (900401, THREAD_ID, USER_ID, "a reply", 900400, BASE.replace(second=1)),
    )
    conn.execute(
        """
        INSERT INTO attachments (
            id, message_id, filename, content_type, size, cached_url, url_expires_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (900500, 900401, "cat.png", "image/png", 1024, "https://cdn.example/cat.png", BASE),
    )
    conn.commit()


def _cleanup(conn):
    conn.execute("DELETE FROM attachments WHERE message_id = %s", (900401,))
    conn.execute("DELETE FROM messages WHERE thread_id = %s", (THREAD_ID,))
    conn.execute("DELETE FROM threads WHERE id = %s", (THREAD_ID,))
    conn.execute("DELETE FROM channels WHERE id = %s", (CHANNEL_ID,))
    conn.execute("DELETE FROM users WHERE id = %s", (USER_ID,))
    conn.commit()


def test_rendered_links_are_prefixed_when_x_forwarded_prefix_is_present(
    page, live_server, seed_conn
):
    _seed(seed_conn)
    try:
        page.set_extra_http_headers({"X-Forwarded-Prefix": PREFIX})

        page.goto(f"{live_server}/")
        board_href = page.locator(".board-row .board-name a").get_attribute("href")
        assert board_href == f"{PREFIX}/board/{CHANNEL_ID}"

        # board_landing now defaults a text channel to continuous browsing
        # (ROADMAP.md's UI polish backlog); this test's actual concern is
        # prefix-rewriting of rendered links, not landing-page defaults, so
        # it goes straight to the topics list where the seeded thread lives.
        page.goto(f"{live_server}/board/{CHANNEL_ID}/topics")
        topic_href = page.locator(".topic-row .topic-name a").get_attribute("href")
        assert topic_href == f"{PREFIX}/topic/{THREAD_ID}/page/1"

        page.goto(f"{live_server}/topic/{THREAD_ID}/page/1")
        permalink_href = page.locator("#post-900400 .post-permalink").get_attribute("href")
        assert permalink_href == f"{PREFIX}/topic/{THREAD_ID}/page/1#post-900400"

        reply_quote_href = page.locator("#post-900401 .reply-quote").get_attribute(
            "data-quoted-message-id"
        )
        assert reply_quote_href == "900400"
        quote_link_href = page.locator("#post-900401 .reply-quote a").get_attribute("href")
        assert quote_link_href == f"{PREFIX}/topic/{THREAD_ID}/page/1#post-900400"

        attachment_href = page.locator("#post-900401 .attachment").get_attribute("href")
        assert attachment_href == f"{PREFIX}/att/900500"

        stylesheet_href = page.locator("link[rel=stylesheet]").get_attribute("href")
        assert stylesheet_href.startswith(f"{PREFIX}/static/")
    finally:
        _cleanup(seed_conn)
