import discord

from threadbare.sync_worker.channel_overwrites import sync_channel_overwrites


class FakePermissionPair:
    def __init__(self, allow: int, deny: int):
        self._allow = allow
        self._deny = deny

    def pair(self):
        return (
            type("P", (), {"value": self._allow})(),
            type("P", (), {"value": self._deny})(),
        )


class FakeOverwriteTargetRole(discord.Role):
    """Subclasses the real discord.Role for isinstance purposes -- see
    tests/unit/sync_worker/test_transform.py's identical fake for why a
    duck-typed double wouldn't exercise the real isinstance branch.
    """

    def __init__(self, id):
        self.id = id


class FakeMember:
    def __init__(self, id, *, display_name="a member", is_bot=False):
        self.id = id
        self.display_name = display_name
        self.avatar = None
        self.bot = is_bot
        self.roles = []


class FakeChannel:
    def __init__(self, *, id, overwrites):
        self.id = id
        self.overwrites = overwrites


async def _seed_guild_channel_and_role(conn, *, guild_id=1, channel_id=10, role_id=500):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, 'general')",
        (channel_id, guild_id),
    )
    await conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (%s, %s, 'Mods', 0, 0, 0)",
        (role_id, guild_id),
    )


async def test_sync_channel_overwrites_persists_role_and_member_tiers(db_conn):
    await _seed_guild_channel_and_role(db_conn)
    role = FakeOverwriteTargetRole(id=500)
    member = FakeMember(id=900)
    channel = FakeChannel(
        id=10,
        overwrites={
            role: FakePermissionPair(0x400, 0x800),
            member: FakePermissionPair(0x1, 0x2),
        },
    )

    await sync_channel_overwrites(db_conn, channel)

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT allow, deny FROM channel_role_overwrites "
            "WHERE channel_id = 10 AND role_id = 500"
        )
        role_row = await cur.fetchone()
        await cur.execute(
            "SELECT allow, deny FROM channel_member_overwrites "
            "WHERE channel_id = 10 AND user_id = 900"
        )
        member_row = await cur.fetchone()
    assert role_row == {"allow": 0x400, "deny": 0x800}
    assert member_row == {"allow": 0x1, "deny": 0x2}


async def test_sync_channel_overwrites_self_heals_a_users_row_for_a_member_target(db_conn):
    # A member-tier overwrite can target someone who has never posted -- no
    # users row exists for them yet. Without the self-heal, the INSERT into
    # channel_member_overwrites would violate its FK on users(id).
    await _seed_guild_channel_and_role(db_conn)
    member = FakeMember(id=901, display_name="never posted")
    channel = FakeChannel(id=10, overwrites={member: FakePermissionPair(0x1, 0x2)})

    await sync_channel_overwrites(db_conn, channel)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT display_name FROM users WHERE id = 901")
        row = await cur.fetchone()
    assert row == {"display_name": "never posted"}


async def test_sync_channel_overwrites_removes_a_row_no_longer_present(db_conn):
    await _seed_guild_channel_and_role(db_conn)
    role = FakeOverwriteTargetRole(id=500)
    channel = FakeChannel(id=10, overwrites={role: FakePermissionPair(0x400, 0x800)})
    await sync_channel_overwrites(db_conn, channel)

    channel.overwrites = {}
    await sync_channel_overwrites(db_conn, channel)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM channel_role_overwrites WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 0
