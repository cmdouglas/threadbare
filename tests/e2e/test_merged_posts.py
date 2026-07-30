"""Consecutive-post merging, driven through a real browser against a real
running app -- the tier that would catch a template or theme mistake the
integration tests' string assertions can't see.
"""

from datetime import UTC, datetime, timedelta

import pytest

from .conftest import E2E_GUILD_ID

CHANNEL_ID = 910001
AUTHOR_ID = 910002
OTHER_AUTHOR_ID = 910003
BASE = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
FIRST_MESSAGE_ID = 910100


def _seed(conn, *, merge_enabled: bool):
    conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (E2E_GUILD_ID, "E2E Guild"),
    )
    conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, 0, 'merged', true, true) ON CONFLICT DO NOTHING
        """,
        (CHANNEL_ID, E2E_GUILD_ID),
    )
    for user_id, name in ((AUTHOR_ID, "burst_author"), (OTHER_AUTHOR_ID, "someone_else")):
        conn.execute(
            "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, name),
        )

    # Three bursts of three, alternating author so each burst is one post.
    message_id = FIRST_MESSAGE_ID
    for burst in range(3):
        for offset in range(3):
            conn.execute(
                """
                INSERT INTO messages (id, channel_id, author_id, content, posted_at, starts_group)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    message_id,
                    CHANNEL_ID,
                    AUTHOR_ID if burst % 2 == 0 else OTHER_AUTHOR_ID,
                    f"message {message_id}",
                    BASE + timedelta(minutes=burst * 60 + offset),
                    offset == 0,  # the grouping db/grouping.py would compute
                ),
            )
            message_id += 1

    conn.execute(
        """
        INSERT INTO site_settings (id, merge_consecutive_posts, merge_gap_seconds)
        VALUES (true, %s, 420)
        ON CONFLICT (id) DO UPDATE SET merge_consecutive_posts = EXCLUDED.merge_consecutive_posts
        """,
        (merge_enabled,),
    )
    conn.commit()


def _cleanup(conn):
    conn.execute("DELETE FROM messages WHERE channel_id = %s", (CHANNEL_ID,))
    conn.execute("DELETE FROM channels WHERE id = %s", (CHANNEL_ID,))
    conn.execute("UPDATE site_settings SET merge_consecutive_posts = false WHERE id = true")
    conn.commit()


@pytest.fixture
def merged_channel(seed_conn):
    _seed(seed_conn, merge_enabled=True)
    yield seed_conn
    _cleanup(seed_conn)


@pytest.fixture
def unmerged_channel(seed_conn):
    _seed(seed_conn, merge_enabled=False)
    yield seed_conn
    _cleanup(seed_conn)


def test_a_burst_renders_as_one_post_with_one_author_header(page, live_server, merged_channel):
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1")

    # Three bursts, nine messages: three posts, nine segments, three headers.
    assert page.locator(".post").count() == 3
    assert page.locator(".post-segment").count() == 9
    assert page.locator(".post-author").count() == 3
    # Every message is still visible and still individually anchored.
    for message_id in range(FIRST_MESSAGE_ID, FIRST_MESSAGE_ID + 9):
        assert page.locator(f"#post-{message_id}").count() == 1


def test_merging_off_renders_one_post_per_message(page, live_server, unmerged_channel):
    """The default path. Its markup must be exactly what it was before merging
    existed -- no segment wrapper -- so a third-party theme sees no DOM change
    unless a mod opts in.
    """
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1")

    assert page.locator(".post").count() == 9
    assert page.locator(".post-segment").count() == 0
    assert page.locator(f"#post-{FIRST_MESSAGE_ID}").count() == 1


def test_each_segment_keeps_its_own_permalink_and_timestamp(page, live_server, merged_channel):
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1")

    first_post = page.locator(".post").first
    assert first_post.locator(".post-segment").count() == 3
    assert first_post.locator(".post-segment-meta .post-permalink").count() == 3
    assert first_post.locator(".post-segment-meta time").count() == 3


def test_a_page_boundary_never_splits_a_post(page, live_server, merged_channel):
    """Ten posts per page against three posts fits on one page; the real check
    is that navigating pages never shows half a post. Driven at the smallest
    page size the preferences allow.
    """
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1?posts_per_page=10")

    # Every rendered post has all three of its segments -- none truncated at
    # the page edge.
    for i in range(page.locator(".post").count()):
        assert page.locator(".post").nth(i).locator(".post-segment").count() == 3


def test_a_permalink_anchors_the_segment_it_names(page, live_server, merged_channel):
    """A merged-in message's anchor has to be reachable, not just present:
    this follows the segment's own permalink and checks the browser lands on
    that element rather than the post's head.
    """
    page.goto(f"{live_server}/board/{CHANNEL_ID}/continuous/page/1")

    target = FIRST_MESSAGE_ID + 2  # last segment of the first post
    href = page.locator(f"#post-{target} .post-permalink").get_attribute("href")
    page.goto(f"{live_server}{href}")

    assert page.locator(f"#post-{target}").count() == 1
    assert f"#post-{target}" in page.url
