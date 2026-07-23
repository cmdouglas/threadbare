from datetime import UTC, datetime, timedelta

from .conftest import run

T1 = datetime(2026, 1, 1, tzinfo=UTC)


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, 0, 'general', true)
        """,
        (channel_id, guild_id),
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


async def _seed_thread_message(conn, *, message_id, thread_id, author_id=100, content, posted_at):
    await conn.execute(
        """
        INSERT INTO messages (id, thread_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (message_id, thread_id, author_id, content, posted_at),
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

    resp = client.get("/topic/3000/jump_to_page")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/topic/3000/page/1"
