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


async def _seed_message(conn, *, message_id, channel_id, author_id, content="hi"):
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (message_id, channel_id, author_id, content),
    )


def test_user_page_returns_404_for_unknown_user(client):
    resp = client.get("/user/999999")

    assert resp.status_code == 404


def test_user_page_shows_display_name_and_post_count(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_user(web_conn, user_id=100, display_name="alice"))
    run(_seed_message(web_conn, message_id=1, channel_id=10, author_id=100, content="my post"))

    resp = client.get("/user/100")

    assert resp.status_code == 200
    assert b"alice" in resp.data
    assert b"1 post" in resp.data
    assert b"my post" in resp.data


def test_user_page_shows_the_profile_avatar_by_default(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_user(web_conn, user_id=100, display_name="alice"))

    resp = client.get("/user/100")

    assert resp.status_code == 200
    assert b'class="user-avatar"' in resp.data
    assert b"cdn.discordapp.com" in resp.data


def test_user_page_hides_the_profile_avatar_when_toggled_off(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_user(web_conn, user_id=100, display_name="alice"))

    resp = client.get("/user/100?avatars=off")

    assert resp.status_code == 200
    assert b'class="user-avatar"' not in resp.data


def test_user_page_with_no_posts(client, web_conn):
    run(_seed_user(web_conn, user_id=100, display_name="alice"))

    resp = client.get("/user/100")

    assert resp.status_code == 200
    assert b"0 posts" in resp.data
    assert b"No posts yet" in resp.data
