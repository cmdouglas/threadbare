from threadbare.discord_permissions import ADMINISTRATOR, READ_MESSAGE_HISTORY, VIEW_CHANNEL
from threadbare.web import authz

from .conftest import run

GUILD_ID = 1
HELD_ROLE_ID = 42
USER_ID = 7

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


async def _seed_guild(conn, *, guild_id=GUILD_ID):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (guild_id, "Test Guild"),
    )


async def _seed_role(conn, *, role_id, guild_id=GUILD_ID, permissions=0):
    await conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (%s, %s, 'a role', 0, 0, %s)",
        (role_id, guild_id, permissions),
    )


async def _seed_channel(conn, *, channel_id, guild_id=GUILD_ID, type=0, parent_id=None):
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, parent_id) "
        "VALUES (%s, %s, %s, 'a channel', %s)",
        (channel_id, guild_id, type, parent_id),
    )


async def _seed_user(conn, *, user_id=USER_ID, role_ids=None):
    await conn.execute(
        "INSERT INTO users (id, display_name, role_ids) VALUES (%s, 'a user', %s) "
        "ON CONFLICT DO NOTHING",
        (user_id, role_ids or []),
    )


async def _seed_channel_role_overwrite(conn, *, channel_id, role_id, allow=0, deny=0):
    await conn.execute(
        "INSERT INTO channel_role_overwrites (channel_id, role_id, allow, deny) "
        "VALUES (%s, %s, %s, %s)",
        (channel_id, role_id, allow, deny),
    )


async def _seed_channel_member_overwrite(conn, *, channel_id, user_id, allow=0, deny=0):
    await conn.execute(
        "INSERT INTO channel_member_overwrites (channel_id, user_id, allow, deny) "
        "VALUES (%s, %s, %s, %s)",
        (channel_id, user_id, allow, deny),
    )


def test_no_special_roles_sees_only_everyone_visible_channels(web_conn):
    async def seed():
        await _seed_guild(web_conn)
        await _seed_role(web_conn, role_id=GUILD_ID, permissions=BOTH_REQUIRED)
        await _seed_user(web_conn)
        await _seed_channel(web_conn, channel_id=10)
        await _seed_channel(web_conn, channel_id=20)
        await _seed_channel_role_overwrite(
            web_conn, channel_id=20, role_id=GUILD_ID, deny=VIEW_CHANNEL
        )

    run(seed())

    visible = run(authz.resolve_visible_channel_ids(web_conn, guild_id=GUILD_ID, user_id=USER_ID))

    assert visible == {10}


def test_category_then_channel_overwrite_precedence(web_conn):
    async def seed():
        await _seed_guild(web_conn)
        await _seed_role(web_conn, role_id=GUILD_ID, permissions=READ_MESSAGE_HISTORY)
        await _seed_role(web_conn, role_id=HELD_ROLE_ID)
        await _seed_user(web_conn, role_ids=[HELD_ROLE_ID])
        await _seed_channel(web_conn, channel_id=30, type=4)  # category
        await _seed_channel(web_conn, channel_id=31, parent_id=30)
        await _seed_channel_role_overwrite(
            web_conn, channel_id=30, role_id=GUILD_ID, deny=VIEW_CHANNEL
        )
        await _seed_channel_role_overwrite(
            web_conn, channel_id=30, role_id=HELD_ROLE_ID, allow=VIEW_CHANNEL
        )

    run(seed())

    visible = run(authz.resolve_visible_channel_ids(web_conn, guild_id=GUILD_ID, user_id=USER_ID))

    assert visible == {31}


def test_member_specific_deny_overrides_role_level_allow(web_conn):
    async def seed():
        await _seed_guild(web_conn)
        await _seed_role(web_conn, role_id=GUILD_ID, permissions=BOTH_REQUIRED)
        await _seed_user(web_conn)
        await _seed_channel(web_conn, channel_id=40)
        await _seed_channel_member_overwrite(
            web_conn, channel_id=40, user_id=USER_ID, deny=VIEW_CHANNEL
        )

    run(seed())

    visible = run(authz.resolve_visible_channel_ids(web_conn, guild_id=GUILD_ID, user_id=USER_ID))

    assert visible == set()


def test_administrator_role_sees_channel_despite_everyone_deny(web_conn):
    async def seed():
        await _seed_guild(web_conn)
        await _seed_role(web_conn, role_id=GUILD_ID)
        await _seed_role(web_conn, role_id=HELD_ROLE_ID, permissions=ADMINISTRATOR)
        await _seed_user(web_conn, role_ids=[HELD_ROLE_ID])
        await _seed_channel(web_conn, channel_id=50)
        await _seed_channel_role_overwrite(
            web_conn, channel_id=50, role_id=GUILD_ID, deny=BOTH_REQUIRED
        )

    run(seed())

    visible = run(authz.resolve_visible_channel_ids(web_conn, guild_id=GUILD_ID, user_id=USER_ID))

    assert visible == {50}


def test_channel_with_no_category_resolves_without_error(web_conn):
    async def seed():
        await _seed_guild(web_conn)
        await _seed_role(web_conn, role_id=GUILD_ID, permissions=BOTH_REQUIRED)
        await _seed_user(web_conn)
        await _seed_channel(web_conn, channel_id=60, parent_id=None)

    run(seed())

    visible = run(authz.resolve_visible_channel_ids(web_conn, guild_id=GUILD_ID, user_id=USER_ID))

    assert visible == {60}


def test_user_with_no_row_yet_falls_back_to_no_roles(web_conn):
    async def seed():
        await _seed_guild(web_conn)
        await _seed_role(web_conn, role_id=GUILD_ID, permissions=BOTH_REQUIRED)
        await _seed_channel(web_conn, channel_id=70)

    run(seed())

    visible = run(authz.resolve_visible_channel_ids(web_conn, guild_id=GUILD_ID, user_id=999999))

    assert visible == {70}
