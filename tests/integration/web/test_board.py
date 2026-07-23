from datetime import UTC, datetime, timedelta

from .conftest import run

T1 = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_guild(conn, *, guild_id=1):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))


async def _seed_board(conn, *, channel_id, guild_id=1, type=0, name="general"):
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, %s, %s, true, true)
        """,
        (channel_id, guild_id, type, name),
    )


async def _seed_thread(conn, *, thread_id, parent_channel_id, name="a thread", created_at=None):
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, %s)",
        (thread_id, parent_channel_id, name, created_at or datetime.now(UTC)),
    )


async def _seed_user(conn, *, user_id=100, display_name="alice"):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (user_id, display_name),
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
    assert b"Page 1 of 2" in resp.data
    assert b"/topic/3000/page/2" in resp.data


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
