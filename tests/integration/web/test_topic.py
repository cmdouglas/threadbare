from datetime import UTC, datetime, timedelta

from threadbare.db import queries
from threadbare.discord_permissions import READ_MESSAGE_HISTORY, VIEW_CHANNEL

from .conftest import run

T1 = datetime(2026, 1, 1, tzinfo=UTC)

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


async def _seed_guild(conn, *, guild_id=1):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))


async def _seed_guild_and_channel(
    conn, *, guild_id=1, channel_id=10, parent_id=None, is_public=True, visibility_enrolled=False
):
    await _seed_guild(conn, guild_id=guild_id)
    await conn.execute(
        """
        INSERT INTO channels
            (id, guild_id, parent_id, type, name, is_public, visibility_enrolled)
        VALUES (%s, %s, %s, 0, 'general', %s, %s)
        """,
        (channel_id, guild_id, parent_id, is_public, visibility_enrolled),
    )


async def _seed_role(
    conn, *, role_id, guild_id=1, name="a role", color=0, position=0, permissions=0
):
    await conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (role_id, guild_id, name, color, position, permissions),
    )


async def _seed_category(conn, *, category_id, guild_id=1, name="A Category"):
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 4, %s)",
        (category_id, guild_id, name),
    )


async def _seed_thread(conn, *, thread_id, parent_channel_id, name="a thread"):
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, now())",
        (thread_id, parent_channel_id, name),
    )


async def _seed_user(conn, *, user_id=100, display_name="alice"):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (user_id, display_name),
    )


async def _seed_thread_message(
    conn, *, message_id, thread_id, author_id=100, content, posted_at, reply_to_id=None
):
    await conn.execute(
        """
        INSERT INTO messages (id, thread_id, author_id, content, posted_at, reply_to_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (message_id, thread_id, author_id, content, posted_at, reply_to_id),
    )


def test_topic_index_redirects_to_page_one(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/1"


def test_topic_page_returns_404_for_unknown_thread(client):
    resp = client.get("/topic/999999/page/1")

    assert resp.status_code == 404


def test_topic_page_returns_404_for_an_enrolled_channel_the_requester_cannot_see(client, web_conn):
    run(_seed_guild_and_channel(web_conn, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/page/1")

    assert resp.status_code == 404


def test_topic_page_succeeds_for_an_enrolled_channel_the_requester_can_see(client, web_conn):
    run(_seed_guild_and_channel(web_conn, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1, permissions=BOTH_REQUIRED))  # @everyone
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/page/1")

    assert resp.status_code == 200


def test_topic_page_renders_messages_with_permalink_anchor(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my thread"))
    run(_seed_user(web_conn))
    run(
        _seed_thread_message(
            web_conn, message_id=1, thread_id=3000, content="hello world", posted_at=T1
        )
    )

    resp = client.get("/topic/3000/page/1")

    assert resp.status_code == 200
    assert b'id="post-1"' in resp.data
    assert b"hello world" in resp.data
    assert b"my thread" in resp.data
    assert b"View on Discord" in resp.data
    assert b'class="jump-to-page" action="/topic/3000/jump_to_page"' in resp.data


def test_topic_page_filters_by_reaction(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(
        _seed_thread_message(
            web_conn, message_id=1, thread_id=3000, content="no reaction", posted_at=T1
        )
    )
    run(
        _seed_thread_message(
            web_conn,
            message_id=2,
            thread_id=3000,
            content="has reaction",
            posted_at=T1 + timedelta(1),
        )
    )
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (2, "🔥", 1)
        )
    )

    resp = client.get("/topic/3000/page/1?reaction=%F0%9F%94%A5")

    assert resp.status_code == 200
    assert b"has reaction" in resp.data
    assert b"no reaction" not in resp.data


def test_topic_page_does_not_mark_read_when_a_reaction_filter_is_active(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(
        _seed_thread_message(
            web_conn, message_id=1, thread_id=3000, content="no reaction", posted_at=T1
        )
    )
    run(
        _seed_thread_message(
            web_conn,
            message_id=2,
            thread_id=3000,
            content="has reaction",
            posted_at=T1 + timedelta(1),
        )
    )
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (2, "🔥", 1)
        )
    )

    resp = client.get("/topic/3000/page/1?reaction=%F0%9F%94%A5")

    assert resp.status_code == 200
    assert run(queries.get_read_marker(web_conn, user_id=1, thread_id=3000)) is None


def test_topic_jump_to_page_preserves_the_reaction_filter(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/jump_to_page?page=2&reaction=%F0%9F%94%A5")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/2?reaction=%F0%9F%94%A5"


def test_topic_page_shows_reaction_filter_picker(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (1, "🔥", 2)
        )
    )

    resp = client.get("/topic/3000/page/1")

    assert resp.status_code == 200
    assert b'class="reaction-filter-option"' in resp.data
    assert b"reaction=%F0%9F%94%A5" in resp.data


def test_topic_page_marks_the_thread_read_up_to_the_last_message_shown(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))
    run(
        _seed_thread_message(
            web_conn, message_id=2, thread_id=3000, content="hi again", posted_at=T1 + timedelta(1)
        )
    )

    resp = client.get("/topic/3000/page/1")

    assert resp.status_code == 200
    marker = run(queries.get_read_marker(web_conn, user_id=1, thread_id=3000))
    assert marker == {"last_read_message_id": 2, "last_read_posted_at": T1 + timedelta(1)}


def test_topic_page_hides_jump_to_unread_link_when_fully_read(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))

    resp = client.get("/topic/3000/page/1")

    assert b'class="jump-to-unread"' not in resp.data


def test_topic_page_hides_jump_to_unread_link_once_caught_up_via_a_later_page(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_thread_message(
                web_conn,
                message_id=i + 1,
                thread_id=3000,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )
    client.get("/topic/3000/page/2")

    resp = client.get("/topic/3000/page/1")

    assert b'class="jump-to-unread"' not in resp.data


def test_topic_page_shows_breadcrumb_to_home_and_channel(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my thread"))

    resp = client.get("/topic/3000/page/1")

    assert b'class="breadcrumbs"' in resp.data
    assert b'<a href="/">Home</a>' in resp.data
    assert b'<a href="/board/10">general</a>' in resp.data


async def _seed_channel(conn, *, channel_id, guild_id=1, parent_id=None, name="general"):
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, parent_id, type, name, is_public)
        VALUES (%s, %s, %s, 0, %s, true)
        """,
        (channel_id, guild_id, parent_id, name),
    )


def test_topic_page_shows_breadcrumb_category_as_unlinked_text(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_category(web_conn, category_id=1, name="Text Channels"))
    run(_seed_channel(web_conn, channel_id=10, parent_id=1))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my thread"))

    resp = client.get("/topic/3000/page/1")

    assert b"<span>Text Channels</span>" in resp.data


def test_topic_page_shows_the_author_avatar_by_default(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn, user_id=100))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))

    resp = client.get("/topic/3000/page/1")

    assert resp.status_code == 200
    assert b'class="post-avatar"' in resp.data
    assert b"cdn.discordapp.com" in resp.data


def test_topic_page_hides_the_author_avatar_when_toggled_off(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn, user_id=100))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))

    resp = client.get("/topic/3000/page/1?avatars=off")

    assert resp.status_code == 200
    assert b'class="post-avatar"' not in resp.data


def test_topic_page_paginates(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_thread_message(
                web_conn,
                message_id=i + 1,
                thread_id=3000,
                content=f"message {i}",
                posted_at=T1 + timedelta(seconds=i),
            )
        )

    page1 = client.get("/topic/3000/page/1")
    page2 = client.get("/topic/3000/page/2")

    assert b'id="post-1"' in page1.data
    assert b'id="post-26"' not in page1.data
    assert b'id="post-26"' in page2.data


def test_topic_page_pagination_shows_ellipsis_gaps_around_the_current_page(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    for i in range(351):  # exactly 15 pages at page_size=25
        run(
            _seed_thread_message(
                web_conn,
                message_id=i + 1,
                thread_id=3000,
                content=f"message {i}",
                posted_at=T1 + timedelta(seconds=i),
            )
        )

    resp = client.get("/topic/3000/page/8")

    assert resp.status_code == 200
    # topic.html includes _pagination.html twice (top and bottom of page),
    # so each of the two gaps in the window shows up twice.
    assert resp.data.count(b"&hellip;") == 4
    assert b'class="pagination-current">8</span>' in resp.data
    for p in (1, 2, 3, 6, 7, 9, 10, 13, 14, 15):
        assert f'class="pagination-page" href="/topic/3000/page/{p}">{p}</a>'.encode() in resp.data
    for p in (4, 5, 11, 12):
        assert f">{p}</a>".encode() not in resp.data


def test_topic_jump_redirects_to_the_page_containing_the_date(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_thread_message(
                web_conn,
                message_id=i + 1,
                thread_id=3000,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )

    resp = client.get(f"/topic/3000/jump?date={(T1 + timedelta(days=26)).strftime('%Y-%m-%d')}")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/2"


def test_topic_jump_to_page_redirects_to_the_requested_page(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/jump_to_page?page=5")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/5"


def test_topic_jump_to_page_clamps_a_missing_or_zero_page_to_one(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/jump_to_page?page=0")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/1"


def test_topic_jump_to_unread_redirects_to_page_one_with_no_marker(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))

    resp = client.get("/topic/3000/jump_to_unread")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/1"


def test_topic_jump_to_unread_lands_on_the_first_unread_message(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_thread_message(
                web_conn,
                message_id=i + 1,
                thread_id=3000,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )
    run(
        queries.mark_read(
            web_conn, user_id=1, thread_id=3000, message_id=25, posted_at=T1 + timedelta(days=24)
        )
    )

    resp = client.get("/topic/3000/jump_to_unread")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/2"


def test_topic_jump_to_unread_returns_404_for_unknown_thread(client, web_conn):
    resp = client.get("/topic/999999/jump_to_unread")

    assert resp.status_code == 404


def test_topic_jump_to_page_defaults_to_page_one_with_no_page_argument(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/jump_to_page")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/1"


def test_topic_jump_returns_404_for_unknown_thread(client, web_conn):
    resp = client.get("/topic/999999/jump?date=2026-01-01")

    assert resp.status_code == 404


def test_topic_jump_returns_404_for_an_enrolled_channel_the_requester_cannot_see(client, web_conn):
    """The redirect's page number is derived from a real message count, so an
    ungated jump leaks roughly how much traffic a channel the requester can't
    read has had -- see the sibling gate tests on /page/ and /tree.
    """
    run(_seed_guild_and_channel(web_conn, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/jump?date=2026-01-01")

    assert resp.status_code == 404


def test_topic_jump_to_unread_returns_404_for_an_enrolled_channel_the_requester_cannot_see(
    client, web_conn
):
    run(_seed_guild_and_channel(web_conn, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/jump_to_unread")

    assert resp.status_code == 404


def test_topic_tree_view_returns_404_for_unknown_thread(client):
    resp = client.get("/topic/999999/tree")

    assert resp.status_code == 404


def test_topic_tree_view_returns_404_for_an_enrolled_channel_the_requester_cannot_see(
    client, web_conn
):
    run(_seed_guild_and_channel(web_conn, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))

    resp = client.get("/topic/3000/tree")

    assert resp.status_code == 404


def test_topic_tree_view_renders_a_reply_nested_under_its_parent(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my thread"))
    run(_seed_user(web_conn))
    run(
        _seed_thread_message(
            web_conn, message_id=1, thread_id=3000, content="root post", posted_at=T1
        )
    )
    run(
        _seed_thread_message(
            web_conn,
            message_id=2,
            thread_id=3000,
            content="a reply",
            posted_at=T1 + timedelta(1),
            reply_to_id=1,
        )
    )

    resp = client.get("/topic/3000/tree")

    assert resp.status_code == 200
    assert b'id="post-1"' in resp.data
    assert b'id="post-2"' in resp.data
    assert b"root post" in resp.data
    assert b"a reply" in resp.data
    assert resp.data.index(b'id="post-1"') < resp.data.index(b'id="post-2"')


def test_topic_tree_view_orders_top_level_messages_chronologically(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="first", posted_at=T1))
    run(
        _seed_thread_message(
            web_conn, message_id=2, thread_id=3000, content="second", posted_at=T1 + timedelta(1)
        )
    )

    resp = client.get("/topic/3000/tree")

    assert resp.status_code == 200
    assert resp.data.index(b'id="post-1"') < resp.data.index(b'id="post-2"')


def test_topic_tree_view_has_a_toggle_link_back_to_the_flat_view(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))

    resp = client.get("/topic/3000/tree")

    assert resp.status_code == 200
    assert b'href="/topic/3000/page/1"' in resp.data


def test_topic_page_has_a_toggle_link_to_the_tree_view(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))

    resp = client.get("/topic/3000/page/1")

    assert resp.status_code == 200
    assert b'href="/topic/3000/tree"' in resp.data


def test_topic_tree_view_marks_the_thread_fully_read(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hi", posted_at=T1))
    run(
        _seed_thread_message(
            web_conn, message_id=2, thread_id=3000, content="hi again", posted_at=T1 + timedelta(1)
        )
    )

    resp = client.get("/topic/3000/tree")

    assert resp.status_code == 200
    marker = run(queries.get_read_marker(web_conn, user_id=1, thread_id=3000))
    assert marker == {"last_read_message_id": 2, "last_read_posted_at": T1 + timedelta(1)}


def test_topic_tree_view_links_a_reply_to_the_correct_flat_view_page(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_thread_message(
                web_conn,
                message_id=i + 1,
                thread_id=3000,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )

    resp = client.get("/topic/3000/tree")

    assert resp.status_code == 200
    # DEFAULT_PAGE_SIZE is 25, so message 30 (id=30, the 30th post) is the
    # 5th post on page 2 -- its own permalink should reflect that, not page 1.
    assert b"/topic/3000/page/2#post-30" in resp.data
