from threadbare.discord_permissions import READ_MESSAGE_HISTORY, VIEW_CHANNEL

from .conftest import run

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


async def _seed_guild_and_channel(
    conn, *, guild_id=1, channel_id=10, is_public=True, visibility_enrolled=False
):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels
            (id, guild_id, type, name, is_public, indexed, visibility_enrolled)
        VALUES (%s, %s, 0, 'general', %s, true, %s)
        """,
        (channel_id, guild_id, is_public, visibility_enrolled),
    )


async def _seed_user(conn, *, user_id, display_name, is_bot=False, role_ids=None):
    await conn.execute(
        "INSERT INTO users (id, display_name, is_bot, role_ids) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (user_id, display_name, is_bot, role_ids or []),
    )


async def _seed_role(
    conn, *, role_id, guild_id=1, name="a role", color=0, position=0, permissions=0
):
    await conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (role_id, guild_id, name, color, position, permissions),
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


def test_user_page_shows_posts_from_an_enrolled_non_public_channel_when_visible(client, web_conn):
    run(_seed_guild_and_channel(web_conn, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1, permissions=BOTH_REQUIRED))  # @everyone
    run(_seed_user(web_conn, user_id=100, display_name="alice"))
    run(_seed_message(web_conn, message_id=1, channel_id=10, author_id=100, content="my post"))

    resp = client.get("/user/100")

    assert b"1 post" in resp.data
    assert b"my post" in resp.data


def test_user_page_hides_posts_from_an_enrolled_non_public_channel_when_not_visible(
    client, web_conn
):
    run(_seed_guild_and_channel(web_conn, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_user(web_conn, user_id=100, display_name="alice"))
    run(_seed_message(web_conn, message_id=1, channel_id=10, author_id=100, content="my post"))

    resp = client.get("/user/100")

    assert b"0 posts" in resp.data
    assert b"my post" not in resp.data


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


def test_user_page_shows_bot_badge_for_a_bot_account(client, web_conn):
    run(_seed_user(web_conn, user_id=100, display_name="a-bot", is_bot=True))

    resp = client.get("/user/100")

    assert resp.status_code == 200
    assert b'class="bot-badge"' in resp.data


def test_user_page_does_not_show_bot_badge_for_a_human_account(client, web_conn):
    run(_seed_user(web_conn, user_id=100, display_name="alice", is_bot=False))

    resp = client.get("/user/100")

    assert resp.status_code == 200
    assert b'class="bot-badge"' not in resp.data


def test_user_page_shows_role_badges(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    run(_seed_role(web_conn, role_id=1, name="Moderators", color=0xFF0000, position=3))
    run(_seed_role(web_conn, role_id=2, name="Members", color=0, position=1))
    run(_seed_user(web_conn, user_id=100, display_name="alice", role_ids=[1, 2]))

    resp = client.get("/user/100")

    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Moderators" in body
    assert "Members" in body
    assert body.index("Moderators") < body.index("Members")  # ordered by position desc


def test_user_page_with_no_roles_shows_no_role_list(client, web_conn):
    run(_seed_user(web_conn, user_id=100, display_name="alice"))

    resp = client.get("/user/100")

    assert resp.status_code == 200
    assert b'class="user-roles"' not in resp.data


def test_user_page_with_no_posts(client, web_conn):
    run(_seed_user(web_conn, user_id=100, display_name="alice"))

    resp = client.get("/user/100")

    assert resp.status_code == 200
    assert b"0 posts" in resp.data
    assert b"No posts yet" in resp.data
