from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord

from threadbare.sync_worker import events, repository
from threadbare.sync_worker.permissions import READ_MESSAGE_HISTORY, VIEW_CHANNEL

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


@dataclass
class FakeAuthor:
    id: int
    display_name: str = "someone"
    avatar: object | None = None


@dataclass
class FakeMessage:
    id: int
    author: FakeAuthor
    content: str = "hello"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    edited_at: datetime | None = None
    reference: object | None = None
    attachments: list = field(default_factory=list)


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10, is_public=False):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, 0, 'general', %s)
        """,
        (channel_id, guild_id, is_public),
    )


async def test_handle_message_create_writes_the_message(db_conn):
    await _seed_guild_and_channel(db_conn)
    message = FakeMessage(id=100, author=FakeAuthor(id=1))

    await events.handle_message_create(db_conn, message, channel_id=10)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT content FROM messages WHERE id = 100")
        assert (await cur.fetchone())["content"] == "hello"


async def test_handle_message_edit_updates_existing_content(db_conn):
    await _seed_guild_and_channel(db_conn)
    author = FakeAuthor(id=1)
    await events.handle_message_create(db_conn, FakeMessage(id=100, author=author), channel_id=10)

    edited = FakeMessage(id=100, author=author, content="edited!")
    await events.handle_message_edit(db_conn, edited, channel_id=10)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT content, count(*) OVER () AS total FROM messages WHERE id = 100")
        row = await cur.fetchone()
        assert row["content"] == "edited!"
        assert row["total"] == 1  # upsert, not a duplicate row


async def test_handle_message_delete_removes_the_row(db_conn):
    await _seed_guild_and_channel(db_conn)
    await events.handle_message_create(
        db_conn, FakeMessage(id=100, author=FakeAuthor(id=1)), channel_id=10
    )

    await events.handle_message_delete(db_conn, 100)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE id = 100")
        assert (await cur.fetchone())["n"] == 0


async def test_handle_bulk_message_delete_removes_all_rows(db_conn):
    await _seed_guild_and_channel(db_conn)
    author = FakeAuthor(id=1)
    for message_id in (100, 101, 102):
        await events.handle_message_create(
            db_conn, FakeMessage(id=message_id, author=author), channel_id=10
        )

    await events.handle_bulk_message_delete(db_conn, [100, 101])

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM messages")
        remaining = {row["id"] for row in await cur.fetchall()}
    assert remaining == {102}


class FakePermissionPair:
    def __init__(self, allow: int, deny: int):
        self._allow = allow
        self._deny = deny

    def pair(self):
        return (
            type("P", (), {"value": self._allow})(),
            type("P", (), {"value": self._deny})(),
        )


class FakeGuildChannelForOverwrites:
    def __init__(self, *, id, guild, category=None, allow=0, deny=0, type=discord.ChannelType.text):
        self.id = id
        self.guild = guild
        self.category = category
        self.type = type
        self._allow = allow
        self._deny = deny

    def overwrites_for(self, role):
        return FakePermissionPair(self._allow, self._deny)


class FakeRole:
    def __init__(self, permissions_value: int):
        self.permissions = type("Perms", (), {"value": permissions_value})()


class FakeGuild:
    def __init__(self, *, default_role, channels):
        self.default_role = default_role
        self.channels = channels


async def test_handle_channel_permissions_changed_sets_is_public(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=False)
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild)

    await events.handle_channel_permissions_changed(db_conn, channel)

    assert await repository.get_channel_is_public(db_conn, 10) is True


async def test_handle_channel_permissions_changed_purges_on_revoke(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (1, "a"))
    await db_conn.execute(
        "INSERT INTO messages (id, channel_id, author_id, content, posted_at) "
        "VALUES (%s, %s, %s, %s, now())",
        (1000, 10, 1, "hi"),
    )
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild, deny=VIEW_CHANNEL)

    await events.handle_channel_permissions_changed(db_conn, channel)

    assert await repository.get_channel_is_public(db_conn, 10) is False
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 0


async def test_handle_role_permissions_changed_recomputes_every_channel(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, is_public) VALUES (%s, 1, 0, %s, true)",
        (10, "general"),
    )
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, is_public) VALUES (%s, 1, 0, %s, true)",
        (11, "random"),
    )

    role = FakeRole(0)  # base permissions now deny everything
    guild = FakeGuild(default_role=role, channels=[])
    channel_a = FakeGuildChannelForOverwrites(id=10, guild=guild)
    channel_b = FakeGuildChannelForOverwrites(id=11, guild=guild)
    category = FakeGuildChannelForOverwrites(id=999, guild=guild, type=discord.ChannelType.category)
    guild.channels = [channel_a, channel_b, category]

    await events.handle_role_permissions_changed(db_conn, guild)

    assert await repository.get_channel_is_public(db_conn, 10) is False
    assert await repository.get_channel_is_public(db_conn, 11) is False
    # the category itself was skipped, not blown up on (it has no row to update)
