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


def _seed_60_messages(web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    run(_seed_user(web_conn))
    for i in range(60):
        run(
            _seed_thread_message(
                web_conn,
                message_id=i + 1,
                thread_id=3000,
                content=f"message {i}",
                posted_at=T1 + timedelta(seconds=i),
            )
        )


def test_topic_page_uses_25_posts_per_page_by_default(client, web_conn):
    _seed_60_messages(web_conn)

    resp = client.get("/topic/3000/page/1")

    assert resp.data.count(b'class="post"') == 25


def test_query_param_posts_per_page_sets_cookie_and_changes_page_size(client, web_conn):
    _seed_60_messages(web_conn)

    resp = client.get("/topic/3000/page/1?posts_per_page=50")

    assert resp.data.count(b'class="post"') == 50
    set_cookie_headers = resp.headers.get_all("Set-Cookie")
    assert any("posts_per_page=50" in header for header in set_cookie_headers)


def test_cookie_alone_persists_posts_per_page_choice_without_query_param(client, web_conn):
    _seed_60_messages(web_conn)
    client.set_cookie("posts_per_page", "10")

    resp = client.get("/topic/3000/page/1")

    assert resp.data.count(b'class="post"') == 10
    assert "Set-Cookie" not in resp.headers


def test_invalid_query_param_posts_per_page_falls_back_to_default_and_does_not_set_cookie(
    client, web_conn
):
    _seed_60_messages(web_conn)

    resp = client.get("/topic/3000/page/1?posts_per_page=999")

    assert resp.data.count(b'class="post"') == 25
    assert "Set-Cookie" not in resp.headers
