from .conftest import run


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, 0, 'general', true, true)
        """,
        (channel_id, guild_id),
    )


async def _seed_user(conn, *, user_id, display_name):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (user_id, display_name),
    )


async def _seed_message(conn, *, message_id, channel_id, author_id, content):
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (message_id, channel_id, author_id, content),
    )


def test_search_with_no_query_shows_empty_form(client):
    resp = client.get("/search")

    assert resp.status_code == 200
    assert b"0 result" not in resp.data


def test_search_finds_matching_messages(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_user(web_conn, user_id=100, display_name="alice"))
    run(
        _seed_message(
            web_conn, message_id=1, channel_id=10, author_id=100, content="a pizza recipe"
        )
    )
    run(_seed_message(web_conn, message_id=2, channel_id=10, author_id=100, content="unrelated"))

    resp = client.get("/search?q=pizza")

    assert resp.status_code == 200
    assert b"1 result" in resp.data
    assert b"alice" in resp.data


def test_search_result_links_into_context(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_user(web_conn, user_id=100, display_name="alice"))
    run(
        _seed_message(
            web_conn, message_id=1, channel_id=10, author_id=100, content="a pizza recipe"
        )
    )

    resp = client.get("/search?q=pizza")

    assert b"/board/10/continuous/page/1#post-1" in resp.data


def test_search_with_no_matches(client, web_conn):
    run(_seed_guild_and_channel(web_conn))

    resp = client.get("/search?q=nonexistentterm")

    assert resp.status_code == 200
    assert b"0 results" in resp.data
