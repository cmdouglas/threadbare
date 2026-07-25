from datetime import UTC, datetime, timedelta

from threadbare.db import queries
from threadbare.discord_permissions import READ_MESSAGE_HISTORY, VIEW_CHANNEL

from .conftest import run

T1 = datetime(2026, 1, 1, tzinfo=UTC)

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


async def _seed_guild(conn, *, guild_id=1):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))


async def _seed_board(
    conn,
    *,
    channel_id,
    guild_id=1,
    type=0,
    name="general",
    parent_id=None,
    is_public=True,
    visibility_enrolled=False,
):
    await conn.execute(
        """
        INSERT INTO channels
            (id, guild_id, parent_id, type, name, is_public, indexed, visibility_enrolled)
        VALUES (%s, %s, %s, %s, %s, %s, true, %s)
        """,
        (channel_id, guild_id, parent_id, type, name, is_public, visibility_enrolled),
    )


async def _seed_category(conn, *, category_id, guild_id=1, name="A Category"):
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 4, %s)",
        (category_id, guild_id, name),
    )


async def _seed_thread(conn, *, thread_id, parent_channel_id, name="a thread", created_at=None):
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, %s)",
        (thread_id, parent_channel_id, name, created_at or datetime.now(UTC)),
    )


async def _seed_user(conn, *, user_id=100, display_name="alice", is_bot=False, role_ids=None):
    await conn.execute(
        "INSERT INTO users (id, display_name, is_bot, role_ids) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (user_id, display_name, is_bot, role_ids or []),
    )


async def _seed_message(
    conn, *, message_id, channel_id, author_id=100, content="hi", posted_at=None
):
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (message_id, channel_id, author_id, content, posted_at or datetime.now(UTC)),
    )


async def _seed_role(
    conn, *, role_id, guild_id=1, name="a role", color=0, position=0, permissions=0
):
    await conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (role_id, guild_id, name, color, position, permissions),
    )


async def _seed_thread_message(conn, *, message_id, thread_id, author_id=100, content="hi"):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (author_id, "alice"),
    )
    await conn.execute(
        """
        INSERT INTO messages (id, thread_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (message_id, thread_id, author_id, content),
    )


def test_board_landing_returns_404_for_unknown_channel(client):
    resp = client.get("/board/999999")

    assert resp.status_code == 404


def test_board_landing_returns_404_for_a_category(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=1, type=4, name="a category"))

    resp = client.get("/board/1")

    assert resp.status_code == 404


def test_board_landing_returns_404_for_a_voice_channel(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=1, type=2, name="a voice channel"))

    resp = client.get("/board/1")

    assert resp.status_code == 404


def test_board_landing_returns_404_for_a_stage_channel(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=1, type=13, name="a stage"))

    resp = client.get("/board/1")

    assert resp.status_code == 404


def test_board_landing_returns_404_for_an_enrolled_channel_the_requester_cannot_see(
    client, web_conn
):
    run(_seed_guild(web_conn))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_board(web_conn, channel_id=10, is_public=False, visibility_enrolled=True))

    resp = client.get("/board/10")

    assert resp.status_code == 404


def test_board_landing_succeeds_for_an_enrolled_channel_the_requester_can_see(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_role(web_conn, role_id=1, permissions=BOTH_REQUIRED))  # @everyone
    run(_seed_board(web_conn, channel_id=10, is_public=False, visibility_enrolled=True))

    resp = client.get("/board/10")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/1"


def test_board_continuous_page_returns_404_for_an_enrolled_channel_the_requester_cannot_see(
    client, web_conn
):
    run(_seed_guild(web_conn))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_board(web_conn, channel_id=10, is_public=False, visibility_enrolled=True))

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 404


def test_board_landing_redirects_forum_channel_to_its_topics_list(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))

    resp = client.get("/board/10")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/topics"


def test_board_landing_redirects_text_channel_to_continuous_browsing(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=0, name="general"))

    resp = client.get("/board/10")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/1"


def test_board_topics_shows_topics_for_a_forum_channel(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hello"))

    resp = client.get("/board/10/topics")

    assert resp.status_code == 200
    assert b"my topic" in resp.data
    assert b"alice" in resp.data
    assert b'class="jump-to-page" action="/board/10/topics"' in resp.data


def test_board_topics_shows_unread_dot_for_a_topic_with_no_read_marker(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hello"))

    resp = client.get("/board/10/topics")

    assert b'class="unread-dot"' in resp.data


def test_board_topics_hides_unread_dot_once_the_topic_is_marked_read(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hello"))
    client.get("/topic/3000/page/1")

    resp = client.get("/board/10/topics")

    assert b'class="unread-dot"' not in resp.data


def test_board_topics_shows_no_unread_dot_for_a_topic_with_no_posts(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))

    resp = client.get("/board/10/topics")

    assert b'class="unread-dot"' not in resp.data


def test_board_topics_shows_column_headers(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))

    resp = client.get("/board/10/topics")

    assert b'<th class="topic-name">Topic</th>' in resp.data
    assert b'<th class="topic-post-count">Posts</th>' in resp.data
    assert b'<th class="topic-last-post">Last post</th>' in resp.data


def test_board_topics_omits_column_headers_when_there_are_no_topics(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))

    resp = client.get("/board/10/topics")

    assert b'class="column-headings"' not in resp.data


def test_board_topics_links_last_post_author_to_their_profile(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hello"))

    resp = client.get("/board/10/topics")

    assert b'<a href="/user/100">alice</a>' in resp.data


def test_board_topics_per_topic_pagination_has_a_jump_to_page_form(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    for i in range(26):
        run(_seed_thread_message(web_conn, message_id=i + 1, thread_id=3000, content=f"msg {i}"))

    resp = client.get("/board/10/topics")

    assert b'class="pagination-bar"' in resp.data
    assert b'class="jump-to-page" action="/topic/3000/jump_to_page"' in resp.data


def test_board_topics_shows_no_pagination_control_for_a_single_page_topic(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hello"))

    resp = client.get("/board/10/topics")

    assert resp.status_code == 200
    assert b"topic-pagination-row" not in resp.data


def test_board_topics_shows_a_pagination_control_for_a_multi_page_topic(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    for i in range(26):
        run(_seed_thread_message(web_conn, message_id=i + 1, thread_id=3000, content=f"msg {i}"))

    resp = client.get("/board/10/topics")

    assert resp.status_code == 200
    assert b"topic-pagination-row" in resp.data
    assert b'class="pagination-page" href="/topic/3000/page/2">2</a>' in resp.data


def test_board_topics_hides_jump_to_unread_link_for_a_topic_never_visited(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    for i in range(26):
        run(_seed_thread_message(web_conn, message_id=i + 1, thread_id=3000, content=f"msg {i}"))

    resp = client.get("/board/10/topics")

    assert resp.status_code == 200
    assert b"topic-pagination-row" in resp.data
    assert b'class="jump-to-unread"' not in resp.data


def test_board_topics_shows_jump_to_unread_link_once_a_topic_is_partially_read(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    for i in range(26):
        run(_seed_thread_message(web_conn, message_id=i + 1, thread_id=3000, content=f"msg {i}"))
    client.get("/topic/3000/page/1")

    resp = client.get("/board/10/topics")

    assert resp.status_code == 200
    assert b'class="jump-to-unread" href="/topic/3000/jump_to_unread"' in resp.data


def test_board_topics_jump_to_unread_link_shows_the_unread_count(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    for i in range(26):
        run(_seed_thread_message(web_conn, message_id=i + 1, thread_id=3000, content=f"msg {i}"))
    client.get("/topic/3000/page/1")

    resp = client.get("/board/10/topics")

    assert b'<span class="unread-count">(1)</span>' in resp.data


def test_board_topics_shows_visited_row_class_once_a_topic_is_opened(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hello"))
    client.get("/topic/3000/page/1")

    resp = client.get("/board/10/topics")

    assert b"topic-row-visited" in resp.data


def test_board_topics_does_not_show_visited_row_class_for_a_never_opened_topic(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    run(_seed_thread_message(web_conn, message_id=1, thread_id=3000, content="hello"))

    resp = client.get("/board/10/topics")

    assert b"topic-row-visited" not in resp.data


def test_board_topics_hides_jump_to_unread_link_once_a_topic_is_fully_read(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum"))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10, name="my topic"))
    for i in range(26):
        run(_seed_thread_message(web_conn, message_id=i + 1, thread_id=3000, content=f"msg {i}"))
    client.get("/topic/3000/page/1")
    client.get("/topic/3000/page/2")

    resp = client.get("/board/10/topics")

    assert resp.status_code == 200
    assert b'class="jump-to-unread"' not in resp.data


def test_board_topics_shows_freeform_controls_for_a_text_channel(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=0, name="general"))

    resp = client.get("/board/10/topics")

    assert resp.status_code == 200
    assert b"Browse continuously" in resp.data
    assert b"Browse by week" in resp.data
    assert b"View topics list" in resp.data


def test_board_continuous_page_shows_freeform_nav_links(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=0, name="general"))

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b"Browse by week" in resp.data
    assert b"View topics list" in resp.data


def test_board_continuous_page_shows_breadcrumb_to_home_when_uncategorized(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=0, name="general"))

    resp = client.get("/board/10/continuous/page/1")

    assert b'class="breadcrumbs"' in resp.data
    assert b'<a href="/">Home</a>' in resp.data


def test_board_continuous_page_shows_breadcrumb_category_as_unlinked_text(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_category(web_conn, category_id=1, name="Text Channels"))
    run(_seed_board(web_conn, channel_id=10, type=0, name="general", parent_id=1))

    resp = client.get("/board/10/continuous/page/1")

    assert b"<span>Text Channels</span>" in resp.data
    assert b">Text Channels</a>" not in resp.data


def test_board_topics_shows_breadcrumb_category(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_category(web_conn, category_id=1, name="Forums"))
    run(_seed_board(web_conn, channel_id=10, type=15, name="a forum", parent_id=1))

    resp = client.get("/board/10/topics")

    assert b"<span>Forums</span>" in resp.data


def test_board_weeks_index_shows_breadcrumb_category(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_category(web_conn, category_id=1, name="Text Channels"))
    run(_seed_board(web_conn, channel_id=10, type=0, name="general", parent_id=1))

    resp = client.get("/board/10/weeks")

    assert b"<span>Text Channels</span>" in resp.data


def test_board_week_page_shows_freeform_nav_links(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, type=0, name="general"))

    resp = client.get("/board/10/week/2026-W01/page/1")

    assert resp.status_code == 200
    assert b"Browse continuously" in resp.data
    assert b"View topics list" in resp.data


def test_board_continuous_index_redirects_to_page_one(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))

    resp = client.get("/board/10/continuous")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/1"


def test_board_continuous_page_renders_messages(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hello there", posted_at=T1))

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b'id="post-1"' in resp.data
    assert b"hello there" in resp.data


def test_board_continuous_page_filters_by_reaction(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="no reaction", posted_at=T1))
    run(
        _seed_message(
            web_conn,
            message_id=2,
            channel_id=10,
            content="has reaction",
            posted_at=T1 + timedelta(1),
        )
    )
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (2, "🔥", 1)
        )
    )

    resp = client.get("/board/10/continuous/page/1?reaction=%F0%9F%94%A5")

    assert resp.status_code == 200
    assert b"has reaction" in resp.data
    assert b"no reaction" not in resp.data


def test_board_continuous_page_does_not_mark_read_when_a_reaction_filter_is_active(
    client, web_conn
):
    # A filtered page's rows[-1] is not the container's true last-shown
    # message -- marking read to it would silently fast-forward the marker
    # past unfiltered messages the requester never actually saw.
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="no reaction", posted_at=T1))
    run(
        _seed_message(
            web_conn,
            message_id=2,
            channel_id=10,
            content="has reaction",
            posted_at=T1 + timedelta(1),
        )
    )
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (2, "🔥", 1)
        )
    )

    resp = client.get("/board/10/continuous/page/1?reaction=%F0%9F%94%A5")

    assert resp.status_code == 200
    assert run(queries.get_read_marker(web_conn, user_id=1, channel_id=10)) is None


def test_board_continuous_jump_to_page_preserves_the_reaction_filter(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))

    resp = client.get("/board/10/continuous/jump_to_page?page=2&reaction=%F0%9F%94%A5")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/2?reaction=%F0%9F%94%A5"


def test_board_continuous_page_shows_reaction_filter_picker(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (1, "🔥", 2)
        )
    )

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b'class="reaction-filter-option"' in resp.data
    assert b"reaction=%F0%9F%94%A5" in resp.data


def test_board_continuous_page_hides_reaction_filter_when_no_reactions_present(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))

    resp = client.get("/board/10/continuous/page/1")

    assert b'class="reaction-filter"' not in resp.data


def test_board_continuous_page_shows_a_clear_link_when_a_reaction_filter_is_active(
    client, web_conn
):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (1, "🔥", 2)
        )
    )

    resp = client.get("/board/10/continuous/page/1?reaction=%F0%9F%94%A5")

    assert resp.status_code == 200
    assert b'class="reaction-filter-clear"' in resp.data


def test_board_continuous_page_marks_the_channel_read_up_to_the_last_message_shown(
    client, web_conn
):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))
    run(
        _seed_message(
            web_conn, message_id=2, channel_id=10, content="hi again", posted_at=T1 + timedelta(1)
        )
    )

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    marker = run(queries.get_read_marker(web_conn, user_id=1, channel_id=10))
    assert marker == {"last_read_message_id": 2, "last_read_posted_at": T1 + timedelta(1)}


def test_board_continuous_page_hides_jump_to_unread_link_when_fully_read(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))

    resp = client.get("/board/10/continuous/page/1")

    assert b'class="jump-to-unread"' not in resp.data


def test_board_continuous_page_shows_jump_to_unread_link_when_more_pages_remain_unread(
    client, web_conn
):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_message(
                web_conn,
                message_id=i + 1,
                channel_id=10,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )

    resp = client.get("/board/10/continuous/page/1")

    assert b'class="jump-to-unread"' in resp.data


def test_board_continuous_page_hides_jump_to_unread_on_a_reaction_filtered_first_visit(
    client, web_conn
):
    """A reaction filter deliberately skips mark_read, so a first visit leaves
    no marker at all -- and the listing pages' rule (which requires a marker,
    because a marker-less jump just re-lands on page 1) has to hold here too.
    Otherwise the link renders pointing at the page you're already on.
    """
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_message(
                web_conn,
                message_id=i + 1,
                channel_id=10,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (2, "🔥", 1)
        )
    )

    resp = client.get("/board/10/continuous/page/1?reaction=%F0%9F%94%A5")

    assert resp.status_code == 200
    assert b'class="jump-to-unread"' not in resp.data


def test_board_continuous_page_hides_jump_to_unread_link_once_caught_up_via_a_later_page(
    client, web_conn
):
    # The bug this guards against: a naive "are there more pages after this
    # one" check would say yes here (page 1 of 2), even though the reader
    # already caught all the way up via page 2 first -- the real signal has
    # to be the read marker's own position, not which page happens to be
    # open right now.
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_message(
                web_conn,
                message_id=i + 1,
                channel_id=10,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )
    client.get("/board/10/continuous/page/2")

    resp = client.get("/board/10/continuous/page/1")

    assert b'class="jump-to-unread"' not in resp.data


def test_board_continuous_page_shows_bot_badge_for_a_bot_author(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn, is_bot=True))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b'class="bot-badge"' in resp.data


def test_board_continuous_page_does_not_show_bot_badge_for_a_human_author(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn, is_bot=False))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b'class="bot-badge"' not in resp.data


def test_board_continuous_page_colors_username_by_highest_colored_role(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_role(web_conn, role_id=1, name="Moderators", color=0xFF0000, position=1))
    run(_seed_user(web_conn, role_ids=[1]))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b'style="color: #ff0000"' in resp.data


def test_board_continuous_page_does_not_color_username_when_no_colored_role(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, content="hi", posted_at=T1))

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b"style=" not in resp.data


def test_board_continuous_page_renders_message_with_unmatched_markdown_delimiter(client, web_conn):
    # Regression test: discord-markdown-ast-parser 1.0.6 raised a TypeError
    # on an unmatched **, __, or ``` instead of falling back to literal text,
    # crashing this whole route with a 500 (see markdown.py's
    # _search_for_closer_always_tuple patch).
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(
        _seed_message(
            web_conn, message_id=1, channel_id=10, content="wait ** hold on", posted_at=T1
        )
    )

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b"wait ** hold on" in resp.data


def test_board_continuous_page_shows_pagination_and_jump_form_on_a_shared_bar(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_message(
                web_conn,
                message_id=i + 1,
                channel_id=10,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )

    resp = client.get("/board/10/continuous/page/1")

    assert resp.status_code == 200
    assert b'class="pagination-bar"' in resp.data
    assert b'class="jump-to-page"' in resp.data


def test_board_continuous_jump_redirects_to_the_right_page(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_message(
                web_conn,
                message_id=i + 1,
                channel_id=10,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )

    jump_date = (T1 + timedelta(days=26)).strftime("%Y-%m-%d")
    resp = client.get(f"/board/10/continuous/jump?date={jump_date}")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/2"


def test_board_continuous_jump_returns_404_for_unknown_channel(client, web_conn):
    resp = client.get("/board/999999/continuous/jump?date=2026-01-01")

    assert resp.status_code == 404


def test_board_continuous_jump_returns_404_for_an_enrolled_channel_the_requester_cannot_see(
    client, web_conn
):
    """The redirect's page number is derived from a real message count, so an
    ungated jump leaks roughly how much traffic a channel the requester can't
    read has had -- see the sibling gate test on /continuous/page/.
    """
    run(_seed_guild(web_conn))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_board(web_conn, channel_id=10, is_public=False, visibility_enrolled=True))

    resp = client.get("/board/10/continuous/jump?date=2026-01-01")

    assert resp.status_code == 404


def test_board_continuous_jump_to_page_redirects_to_the_requested_page(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))

    resp = client.get("/board/10/continuous/jump_to_page?page=5")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/5"


def test_board_continuous_jump_to_page_clamps_a_missing_or_zero_page_to_one(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))

    resp = client.get("/board/10/continuous/jump_to_page?page=0")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/1"


def test_board_continuous_jump_to_unread_redirects_to_page_one_with_no_marker(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    run(_seed_message(web_conn, message_id=1, channel_id=10, posted_at=T1))

    resp = client.get("/board/10/continuous/jump_to_unread")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/1"


def test_board_continuous_jump_to_unread_lands_on_the_first_unread_message(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    for i in range(30):
        run(
            _seed_message(
                web_conn,
                message_id=i + 1,
                channel_id=10,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )
    run(
        queries.mark_read(
            web_conn, user_id=1, channel_id=10, message_id=25, posted_at=T1 + timedelta(days=24)
        )
    )

    resp = client.get("/board/10/continuous/jump_to_unread")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/2"


def test_board_continuous_jump_to_unread_clamps_to_the_last_page_when_fully_read(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    for i in range(25):
        run(
            _seed_message(
                web_conn,
                message_id=i + 1,
                channel_id=10,
                content=f"message {i}",
                posted_at=T1 + timedelta(days=i),
            )
        )
    run(
        queries.mark_read(
            web_conn, user_id=1, channel_id=10, message_id=25, posted_at=T1 + timedelta(days=24)
        )
    )

    resp = client.get("/board/10/continuous/jump_to_unread")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/continuous/page/1"


def test_board_weeks_index_lists_weeks_with_counts(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    monday_week_28 = datetime(2026, 7, 6, tzinfo=UTC)
    run(_seed_message(web_conn, message_id=1, channel_id=10, posted_at=monday_week_28))
    run(
        _seed_message(
            web_conn, message_id=2, channel_id=10, posted_at=monday_week_28 + timedelta(days=1)
        )
    )

    resp = client.get("/board/10/weeks")

    assert resp.status_code == 200
    assert b"2026-W28" in resp.data


def test_board_week_page_shows_only_messages_in_that_week(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    monday_week_28 = datetime(2026, 7, 6, tzinfo=UTC)
    run(
        _seed_message(
            web_conn, message_id=1, channel_id=10, content="in week 28", posted_at=monday_week_28
        )
    )
    run(
        _seed_message(
            web_conn,
            message_id=2,
            channel_id=10,
            content="in week 29",
            posted_at=monday_week_28 + timedelta(days=7),
        )
    )

    resp = client.get("/board/10/week/2026-W28/page/1")

    assert resp.status_code == 200
    assert b"in week 28" in resp.data
    assert b"in week 29" not in resp.data


def test_board_week_page_filters_by_reaction(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    monday_week_28 = datetime(2026, 7, 6, tzinfo=UTC)
    run(
        _seed_message(
            web_conn, message_id=1, channel_id=10, content="no reaction", posted_at=monday_week_28
        )
    )
    run(
        _seed_message(
            web_conn,
            message_id=2,
            channel_id=10,
            content="has reaction",
            posted_at=monday_week_28 + timedelta(hours=1),
        )
    )
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (2, "🔥", 1)
        )
    )

    resp = client.get("/board/10/week/2026-W28/page/1?reaction=%F0%9F%94%A5")

    assert resp.status_code == 200
    assert b"has reaction" in resp.data
    assert b"no reaction" not in resp.data


def test_board_week_jump_to_page_preserves_the_reaction_filter(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))

    resp = client.get("/board/10/week/2026-W28/jump_to_page?page=2&reaction=%F0%9F%94%A5")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/week/2026-W28/page/2?reaction=%F0%9F%94%A5"


def test_board_week_page_marks_the_channel_read_up_to_the_last_message_shown(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    monday_week_28 = datetime(2026, 7, 6, tzinfo=UTC)
    run(
        _seed_message(
            web_conn, message_id=1, channel_id=10, content="in week 28", posted_at=monday_week_28
        )
    )

    resp = client.get("/board/10/week/2026-W28/page/1")

    assert resp.status_code == 200
    marker = run(queries.get_read_marker(web_conn, user_id=1, channel_id=10))
    assert marker == {"last_read_message_id": 1, "last_read_posted_at": monday_week_28}


def test_board_week_page_hides_jump_to_unread_link_when_the_whole_channel_is_read(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    monday_week_28 = datetime(2026, 7, 6, tzinfo=UTC)
    run(
        _seed_message(
            web_conn, message_id=1, channel_id=10, content="in week 28", posted_at=monday_week_28
        )
    )

    resp = client.get("/board/10/week/2026-W28/page/1")

    assert b'class="jump-to-unread"' not in resp.data


def test_board_week_page_shows_jump_to_unread_link_for_unread_content_in_a_later_week(
    client, web_conn
):
    # This week's own page is "fully read" in isolation, but the channel as
    # a whole (where the marker actually lives) still has unread content in
    # a later week -- the channel-wide check has to catch that, not just
    # whether this week's own page happens to be the last one.
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))
    run(_seed_user(web_conn))
    monday_week_28 = datetime(2026, 7, 6, tzinfo=UTC)
    run(
        _seed_message(
            web_conn, message_id=1, channel_id=10, content="in week 28", posted_at=monday_week_28
        )
    )
    run(
        _seed_message(
            web_conn,
            message_id=2,
            channel_id=10,
            content="in week 29",
            posted_at=monday_week_28 + timedelta(days=7),
        )
    )

    resp = client.get("/board/10/week/2026-W28/page/1")

    assert b'class="jump-to-unread"' in resp.data


def test_board_week_jump_to_page_redirects_to_the_requested_page(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10))

    resp = client.get("/board/10/week/2026-W28/jump_to_page?page=3")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/board/10/week/2026-W28/page/3"
