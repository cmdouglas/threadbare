from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord

from threadbare.sync_worker import repository
from threadbare.sync_worker.discovery import discover_active_threads, discover_channels
from threadbare.sync_worker.permissions import READ_MESSAGE_HISTORY, VIEW_CHANNEL

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


class FakePermissionPair:
    def __init__(self, allow: int, deny: int):
        self._allow = allow
        self._deny = deny

    def pair(self):
        return (
            type("P", (), {"value": self._allow})(),
            type("P", (), {"value": self._deny})(),
        )


class FakeChannel:
    def __init__(
        self,
        *,
        id,
        name,
        guild,
        type=discord.ChannelType.text,
        category=None,
        topic=None,
        position=0,
        allow=0,
        deny=0,
    ):
        self.id = id
        self.name = name
        self.guild = guild
        self.type = type
        self.category = category
        self.category_id = category.id if category else None
        self.topic = topic
        self.position = position
        self._allow = allow
        self._deny = deny

    def overwrites_for(self, role):
        return FakePermissionPair(self._allow, self._deny)


class FakeRole:
    def __init__(self, permissions_value: int):
        self.permissions = type("Perms", (), {"value": permissions_value})()


class FakeGuild:
    def __init__(self, *, id, name, default_role, channels, icon=None, threads=()):
        self.id = id
        self.name = name
        self.default_role = default_role
        self._channels = channels
        self.icon = icon
        self._threads = list(threads)

    async def fetch_channels(self):
        return self._channels

    async def active_threads(self):
        return self._threads


@dataclass
class FakeThread:
    id: int
    parent_id: int
    name: str = "a thread"
    archived: bool = False
    created_at: datetime | None = field(default_factory=lambda: datetime.now(UTC))
    message_count: int = 0


class FakeClient:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild

    async def fetch_guild(self, guild_id):
        return self._guild


async def test_discover_channels_creates_guild_and_channel_rows(db_conn):
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    channel = FakeChannel(id=10, name="general", guild=guild, topic="chat")
    guild._channels = [channel]
    client = FakeClient(guild)

    discovered = await discover_channels(client, db_conn, guild_id=1)

    assert discovered == [10]
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name FROM guilds WHERE id = 1")
        assert (await cur.fetchone())["name"] == "Test Guild"
        await cur.execute("SELECT name, topic, is_public, indexed FROM channels WHERE id = 10")
        row = await cur.fetchone()
    assert row["name"] == "general"
    assert row["topic"] == "chat"
    assert row["is_public"] is True  # computed via refresh_channel_public_status
    assert row["indexed"] is True


async def test_discover_channels_creates_a_row_for_categories_but_excludes_them_from_the_result(
    db_conn,
):
    # Categories still need a row — channels.parent_id is a self-referencing
    # FK, so a child channel pointing at this category would otherwise fail
    # to insert — but they're excluded from discover_channels' return value
    # and never get is_public computed.
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    category = FakeChannel(id=99, name="Category", guild=guild, type=discord.ChannelType.category)
    guild._channels = [category]
    client = FakeClient(guild)

    discovered = await discover_channels(client, db_conn, guild_id=1)

    assert discovered == []
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT is_public FROM channels WHERE id = 99")
        row = await cur.fetchone()
    assert row is not None
    assert row["is_public"] is False  # schema default; never computed for categories


async def test_discover_channels_inserts_category_before_its_child_channel(db_conn):
    # Regression test for the FK-ordering bug: fetch_channels() doesn't
    # guarantee categories come before their children.
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    category = FakeChannel(id=99, name="Category", guild=guild, type=discord.ChannelType.category)
    child = FakeChannel(id=10, name="general", guild=guild, category=category)
    guild._channels = [child, category]  # child listed first, deliberately
    client = FakeClient(guild)

    discovered = await discover_channels(client, db_conn, guild_id=1)

    assert discovered == [10]
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT parent_id FROM channels WHERE id = 10")
        assert (await cur.fetchone())["parent_id"] == 99


async def test_discover_channels_computes_is_public_per_channel(db_conn):
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    public_channel = FakeChannel(id=10, name="general", guild=guild)
    private_channel = FakeChannel(id=11, name="mod-only", guild=guild, deny=VIEW_CHANNEL)
    guild._channels = [public_channel, private_channel]
    client = FakeClient(guild)

    await discover_channels(client, db_conn, guild_id=1)

    assert await repository.get_channel_is_public(db_conn, 10) is True
    assert await repository.get_channel_is_public(db_conn, 11) is False


async def test_discover_channels_computes_is_public_for_forum_channels_like_any_other_channel(
    db_conn,
):
    # Forum channels have no top-level *messages* of their own (everything
    # lives in child threads) but they're still a real channel with real
    # @everyone overwrites — is_public must compute normally so thread
    # discovery/backfill can gate forum-parented threads off it.
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    forum = FakeChannel(id=10, name="a-forum", guild=guild, type=discord.ChannelType.forum)
    guild._channels = [forum]
    client = FakeClient(guild)

    discovered = await discover_channels(client, db_conn, guild_id=1)

    assert discovered == [10]
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT is_public FROM channels WHERE id = 10")
        row = await cur.fetchone()
    assert row is not None
    assert row["is_public"] is True


async def test_discover_channels_does_not_clobber_indexed_on_rediscovery(db_conn):
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    channel = FakeChannel(id=10, name="general", guild=guild)
    guild._channels = [channel]
    client = FakeClient(guild)

    await discover_channels(client, db_conn, guild_id=1)
    await db_conn.execute("UPDATE channels SET indexed = false WHERE id = 10")

    channel.name = "general-renamed"
    await discover_channels(client, db_conn, guild_id=1)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name, indexed FROM channels WHERE id = 10")
        row = await cur.fetchone()
    assert row["name"] == "general-renamed"
    assert row["indexed"] is False


async def test_discover_active_threads_upserts_a_thread_of_a_public_indexed_channel(db_conn):
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    channel = FakeChannel(id=10, name="general", guild=guild)
    thread = FakeThread(id=3000, parent_id=10, name="a thread")
    guild._channels = [channel]
    guild._threads = [thread]
    client = FakeClient(guild)
    await discover_channels(client, db_conn, guild_id=1)

    discovered = await discover_active_threads(client, db_conn, guild_id=1)

    assert discovered == [3000]
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT parent_channel_id, name FROM threads WHERE id = 3000")
        row = await cur.fetchone()
    assert row == {"parent_channel_id": 10, "name": "a thread"}


async def test_discover_active_threads_skips_a_thread_of_a_non_public_channel(db_conn):
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    channel = FakeChannel(id=10, name="mod-only", guild=guild, deny=VIEW_CHANNEL)
    thread = FakeThread(id=3000, parent_id=10)
    guild._channels = [channel]
    guild._threads = [thread]
    client = FakeClient(guild)
    await discover_channels(client, db_conn, guild_id=1)

    discovered = await discover_active_threads(client, db_conn, guild_id=1)

    assert discovered == []
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM threads WHERE id = 3000")
        assert (await cur.fetchone())["n"] == 0


async def test_discover_active_threads_skips_a_thread_of_a_non_indexed_channel(db_conn):
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    channel = FakeChannel(id=10, name="general", guild=guild)
    thread = FakeThread(id=3000, parent_id=10)
    guild._channels = [channel]
    guild._threads = [thread]
    client = FakeClient(guild)
    await discover_channels(client, db_conn, guild_id=1)
    await db_conn.execute("UPDATE channels SET indexed = false WHERE id = 10")

    discovered = await discover_active_threads(client, db_conn, guild_id=1)

    assert discovered == []


async def test_discover_active_threads_discovers_a_thread_of_a_forum_channel(db_conn):
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    forum = FakeChannel(id=10, name="a-forum", guild=guild, type=discord.ChannelType.forum)
    thread = FakeThread(id=3000, parent_id=10)
    guild._channels = [forum]
    guild._threads = [thread]
    client = FakeClient(guild)
    await discover_channels(client, db_conn, guild_id=1)  # now computes is_public=True for forum

    discovered = await discover_active_threads(client, db_conn, guild_id=1)

    assert discovered == [3000]
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM threads WHERE id = 3000")
        assert (await cur.fetchone())["n"] == 1


async def test_discover_active_threads_skips_a_thread_of_an_undiscovered_channel(db_conn):
    # Defensive: shouldn't happen in practice (channel discovery runs first),
    # but a thread whose parent has no channels row yet has nothing to key
    # visibility off of.
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(id=1, name="Test Guild", default_role=role, channels=[])
    thread = FakeThread(id=3000, parent_id=999)
    guild._threads = [thread]
    client = FakeClient(guild)
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))

    discovered = await discover_active_threads(client, db_conn, guild_id=1)

    assert discovered == []
