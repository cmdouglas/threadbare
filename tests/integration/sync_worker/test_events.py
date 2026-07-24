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
    bot: bool = False


@dataclass
class FakeMessage:
    id: int
    author: FakeAuthor
    content: str = "hello"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    edited_at: datetime | None = None
    reference: object | None = None
    attachments: list = field(default_factory=list)
    reactions: list = field(default_factory=list)
    embeds: list = field(default_factory=list)


@dataclass
class FakeThread:
    id: int
    parent_id: int
    name: str = "a thread"
    archived: bool = False
    created_at: datetime | None = field(default_factory=lambda: datetime.now(UTC))
    message_count: int = 0


@dataclass
class FakeReaction:
    emoji: str
    count: int


@dataclass
class FakeEmbed:
    type: str | None = "rich"
    title: str | None = None
    description: str | None = None
    url: str | None = None
    color: object | None = None
    author: object | None = None
    footer: object | None = None
    image: object | None = None
    thumbnail: object | None = None
    video: object | None = None
    fields: list = field(default_factory=list)


async def _seed_guild_and_channel(
    conn, *, guild_id=1, channel_id=10, is_public=False, visibility_enrolled=False
):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, visibility_enrolled)
        VALUES (%s, %s, 0, 'general', %s, %s)
        """,
        (channel_id, guild_id, is_public, visibility_enrolled),
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


async def test_handle_message_create_for_a_thread_upserts_the_thread_row_first(db_conn):
    # Reproduces the live bug: a thread message arriving with no pre-existing
    # threads row must not raise a ForeignKeyViolation on messages.thread_id.
    await _seed_guild_and_channel(db_conn, channel_id=10, is_public=True)
    thread = FakeThread(id=3000, parent_id=10, name="a thread")
    message = FakeMessage(id=100, author=FakeAuthor(id=1))

    await events.handle_message_create(db_conn, message, thread_id=3000, thread=thread)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT parent_channel_id, name FROM threads WHERE id = 3000")
        thread_row = await cur.fetchone()
        assert thread_row == {"parent_channel_id": 10, "name": "a thread"}

        await cur.execute("SELECT channel_id, thread_id, content FROM messages WHERE id = 100")
        message_row = await cur.fetchone()
        assert message_row == {"channel_id": None, "thread_id": 3000, "content": "hello"}


async def test_handle_message_edit_for_a_thread_reuses_existing_thread_row(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10, is_public=True)
    thread = FakeThread(id=3000, parent_id=10, name="a thread")
    author = FakeAuthor(id=1)
    await events.handle_message_create(
        db_conn, FakeMessage(id=100, author=author), thread_id=3000, thread=thread
    )

    edited = FakeMessage(id=100, author=author, content="edited!")
    await events.handle_message_edit(db_conn, edited, thread_id=3000, thread=thread)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT content FROM messages WHERE id = 100")
        assert (await cur.fetchone())["content"] == "edited!"


async def test_handle_thread_upsert_inserts_a_row_for_an_in_scope_parent(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10, is_public=True)
    thread = FakeThread(id=3000, parent_id=10, name="a thread")

    await events.handle_thread_upsert(db_conn, thread)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT parent_channel_id, name FROM threads WHERE id = 3000")
        row = await cur.fetchone()
    assert row == {"parent_channel_id": 10, "name": "a thread"}


async def test_handle_thread_upsert_is_a_no_op_for_a_non_public_parent(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10, is_public=False)
    thread = FakeThread(id=3000, parent_id=10)

    await events.handle_thread_upsert(db_conn, thread)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM threads WHERE id = 3000")
        assert (await cur.fetchone())["n"] == 0


async def test_handle_thread_upsert_inserts_a_row_for_a_visibility_enrolled_non_public_parent(
    db_conn,
):
    await _seed_guild_and_channel(db_conn, channel_id=10, is_public=False, visibility_enrolled=True)
    thread = FakeThread(id=3000, parent_id=10, name="a thread")

    await events.handle_thread_upsert(db_conn, thread)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT parent_channel_id, name FROM threads WHERE id = 3000")
        row = await cur.fetchone()
    assert row == {"parent_channel_id": 10, "name": "a thread"}


async def test_handle_thread_upsert_is_a_no_op_for_an_unknown_parent(db_conn):
    thread = FakeThread(id=3000, parent_id=999)

    await events.handle_thread_upsert(db_conn, thread)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM threads WHERE id = 3000")
        assert (await cur.fetchone())["n"] == 0


async def test_handle_thread_upsert_updates_an_existing_thread(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10, is_public=True)
    await events.handle_thread_upsert(db_conn, FakeThread(id=3000, parent_id=10, name="original"))

    await events.handle_thread_upsert(
        db_conn, FakeThread(id=3000, parent_id=10, name="renamed", archived=True)
    )

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name, archived FROM threads WHERE id = 3000")
        row = await cur.fetchone()
    assert row == {"name": "renamed", "archived": True}


async def test_handle_thread_delete_removes_the_row_and_cascades(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10, is_public=True)
    await events.handle_thread_upsert(db_conn, FakeThread(id=3000, parent_id=10))
    await events.handle_message_create(
        db_conn, FakeMessage(id=100, author=FakeAuthor(id=1)), thread_id=3000
    )

    await events.handle_thread_delete(db_conn, 3000)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM threads WHERE id = 3000")
        assert (await cur.fetchone())["n"] == 0
        await cur.execute("SELECT count(*) AS n FROM messages WHERE thread_id = 3000")
        assert (await cur.fetchone())["n"] == 0


async def test_handle_thread_delete_is_a_no_op_for_unknown_id(db_conn):
    await events.handle_thread_delete(db_conn, 999999)  # should not raise


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


async def test_handle_reaction_add_is_a_no_op_for_an_unknown_message(db_conn):
    # Regression test for the FK-violation risk: a reaction event for a
    # message we never stored must not raise.
    await events.handle_reaction_add(db_conn, message_id=999999, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM reactions WHERE message_id = 999999")
        assert (await cur.fetchone())["n"] == 0


async def test_handle_reaction_add_inserts_a_row_for_a_known_message(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await events.handle_message_create(
        db_conn, FakeMessage(id=100, author=FakeAuthor(id=1)), channel_id=10
    )

    await events.handle_reaction_add(db_conn, message_id=100, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT count FROM reactions WHERE message_id = 100 AND emoji = %s", ("👍",)
        )
        assert (await cur.fetchone())["count"] == 1


async def test_handle_reaction_remove_decrements_an_existing_row(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await events.handle_message_create(
        db_conn, FakeMessage(id=100, author=FakeAuthor(id=1)), channel_id=10
    )
    await events.handle_reaction_add(db_conn, message_id=100, emoji="👍")
    await events.handle_reaction_add(db_conn, message_id=100, emoji="👍")

    await events.handle_reaction_remove(db_conn, message_id=100, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT count FROM reactions WHERE message_id = 100 AND emoji = %s", ("👍",)
        )
        assert (await cur.fetchone())["count"] == 1


async def test_handle_reaction_remove_is_a_no_op_for_an_unknown_message(db_conn):
    await events.handle_reaction_remove(db_conn, message_id=999999, emoji="👍")  # should not raise


async def test_handle_reaction_clear_removes_all_rows(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await events.handle_message_create(
        db_conn, FakeMessage(id=100, author=FakeAuthor(id=1)), channel_id=10
    )
    await events.handle_reaction_add(db_conn, message_id=100, emoji="👍")
    await events.handle_reaction_add(db_conn, message_id=100, emoji="🎉")

    await events.handle_reaction_clear(db_conn, 100)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM reactions WHERE message_id = 100")
        assert (await cur.fetchone())["n"] == 0


async def test_handle_reaction_clear_emoji_removes_only_that_emoji(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await events.handle_message_create(
        db_conn, FakeMessage(id=100, author=FakeAuthor(id=1)), channel_id=10
    )
    await events.handle_reaction_add(db_conn, message_id=100, emoji="👍")
    await events.handle_reaction_add(db_conn, message_id=100, emoji="🎉")

    await events.handle_reaction_clear_emoji(db_conn, message_id=100, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT emoji FROM reactions WHERE message_id = 100")
        remaining = {row["emoji"] for row in await cur.fetchall()}
    assert remaining == {"🎉"}


async def test_write_message_syncs_reactions_to_match_message_reactions(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    author = FakeAuthor(id=1)
    await events.handle_message_create(
        db_conn,
        FakeMessage(id=100, author=author, reactions=[FakeReaction(emoji="👍", count=3)]),
        channel_id=10,
    )

    # A re-fetched Message (backfill/reconciliation/edit) reflects Discord's
    # current state — 👍 dropped to 2, a new 🎉 appeared.
    edited = FakeMessage(
        id=100,
        author=author,
        reactions=[FakeReaction(emoji="👍", count=2), FakeReaction(emoji="🎉", count=1)],
    )
    await events.handle_message_edit(db_conn, edited, channel_id=10)

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT emoji, count FROM reactions WHERE message_id = 100 ORDER BY emoji"
        )
        rows = await cur.fetchall()
    assert {(row["emoji"], row["count"]) for row in rows} == {("👍", 2), ("🎉", 1)}


async def test_write_message_with_no_reactions_clears_any_existing_rows(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    author = FakeAuthor(id=1)
    await events.handle_message_create(
        db_conn,
        FakeMessage(id=100, author=author, reactions=[FakeReaction(emoji="👍", count=3)]),
        channel_id=10,
    )

    await events.handle_message_edit(
        db_conn, FakeMessage(id=100, author=author, reactions=[]), channel_id=10
    )

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM reactions WHERE message_id = 100")
        assert (await cur.fetchone())["n"] == 0


async def test_write_message_syncs_embeds_to_match_message_embeds(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    author = FakeAuthor(id=1)
    await events.handle_message_create(
        db_conn,
        FakeMessage(id=100, author=author, embeds=[FakeEmbed(title="first")]),
        channel_id=10,
    )

    # A re-fetched Message (backfill/reconciliation/edit) reflects Discord's
    # current embed set exactly, same self-healing shape as reactions.
    edited = FakeMessage(
        id=100,
        author=author,
        embeds=[FakeEmbed(title="replaced"), FakeEmbed(title="second")],
    )
    await events.handle_message_edit(db_conn, edited, channel_id=10)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT title FROM embeds WHERE message_id = 100 ORDER BY position")
        rows = await cur.fetchall()
    assert [row["title"] for row in rows] == ["replaced", "second"]


async def test_write_message_with_no_embeds_clears_any_existing_rows(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    author = FakeAuthor(id=1)
    await events.handle_message_create(
        db_conn,
        FakeMessage(id=100, author=author, embeds=[FakeEmbed(title="first")]),
        channel_id=10,
    )

    await events.handle_message_edit(
        db_conn, FakeMessage(id=100, author=author, embeds=[]), channel_id=10
    )

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM embeds WHERE message_id = 100")
        assert (await cur.fetchone())["n"] == 0


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
    def __init__(
        self,
        *,
        id,
        guild,
        category=None,
        category_id=None,
        name="a channel",
        position=0,
        topic=None,
        allow=0,
        deny=0,
        type=discord.ChannelType.text,
        overwrites=None,
        bot_permissions_value=None,
    ):
        self.id = id
        self.guild = guild
        self.category = category
        if category_id is not None:
            self.category_id = category_id
        else:
            self.category_id = category.id if category else None
        self.name = name
        self.position = position
        self.topic = topic
        self.type = type
        self._allow = allow
        self._deny = deny
        self.overwrites = overwrites if overwrites is not None else {}
        self._bot_permissions_value = (
            bot_permissions_value if bot_permissions_value is not None else BOTH_REQUIRED
        )

    def overwrites_for(self, role):
        return FakePermissionPair(self._allow, self._deny)

    def permissions_for(self, member):
        return type("P", (), {"value": self._bot_permissions_value})()


class FakeRole:
    def __init__(self, permissions_value: int):
        self.permissions = type("Perms", (), {"value": permissions_value})()


class FakeOverwriteTargetRole(discord.Role):
    """Subclasses the real discord.Role for isinstance purposes -- see
    tests/unit/sync_worker/test_transform.py's identical fake for why a
    duck-typed double wouldn't exercise channel_overwrite_rows' real
    isinstance branch.
    """

    def __init__(self, id):
        self.id = id


class FakeGuild:
    def __init__(self, *, default_role, channels, me=None):
        self.default_role = default_role
        self.channels = channels
        self.me = me if me is not None else object()


async def test_handle_channel_permissions_changed_sets_is_public(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=False)
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild)

    await events.handle_channel_permissions_changed(db_conn, channel)

    assert await repository.get_channel_is_public(db_conn, 10) is True


async def test_handle_channel_permissions_changed_sets_bot_can_read(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=False)
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild, bot_permissions_value=0)

    await events.handle_channel_permissions_changed(db_conn, channel)

    assert await repository.get_channel_bot_can_read(db_conn, 10) is False


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


async def test_handle_channel_permissions_changed_syncs_role_overwrites(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=False)
    await db_conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (500, 1, 'Mods', 0, 0, 0)"
    )
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    overwrite_role = FakeOverwriteTargetRole(id=500)
    channel = FakeGuildChannelForOverwrites(
        id=10, guild=guild, overwrites={overwrite_role: FakePermissionPair(0x400, 0x800)}
    )

    await events.handle_channel_permissions_changed(db_conn, channel)

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT allow, deny FROM channel_role_overwrites "
            "WHERE channel_id = 10 AND role_id = 500"
        )
        row = await cur.fetchone()
    assert row == {"allow": 0x400, "deny": 0x800}


async def test_handle_channel_permissions_changed_removes_overwrites_no_longer_present(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=False)
    await db_conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (500, 1, 'Mods', 0, 0, 0)"
    )
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    overwrite_role = FakeOverwriteTargetRole(id=500)
    channel = FakeGuildChannelForOverwrites(
        id=10, guild=guild, overwrites={overwrite_role: FakePermissionPair(0x400, 0x800)}
    )
    await events.handle_channel_permissions_changed(db_conn, channel)

    channel.overwrites = {}  # the mod removed the overwrite on Discord
    await events.handle_channel_permissions_changed(db_conn, channel)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM channel_role_overwrites WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 0


async def _channel_row(conn, channel_id):
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, parent_id, type, name, position, topic, is_public, indexed "
            "FROM channels WHERE id = %s",
            (channel_id,),
        )
        return await cur.fetchone()


async def test_handle_channel_upsert_inserts_a_new_row(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(
        id=10, guild=guild, name="general", position=2, topic="chat here"
    )

    await events.handle_channel_upsert(db_conn, channel, guild_id=1)

    row = await _channel_row(db_conn, 10)
    assert row["name"] == "general"
    assert row["position"] == 2
    assert row["topic"] == "chat here"


async def test_handle_channel_upsert_updates_existing_topic_and_name(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(
        id=10, guild=guild, name="renamed", position=1, topic="a new topic"
    )

    await events.handle_channel_upsert(db_conn, channel, guild_id=1)

    row = await _channel_row(db_conn, 10)
    assert row["name"] == "renamed"
    assert row["topic"] == "a new topic"


async def test_handle_channel_upsert_skips_voice_and_stage_voice(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(id=20, guild=guild, type=discord.ChannelType.voice)

    await events.handle_channel_upsert(db_conn, channel, guild_id=1)

    assert await _channel_row(db_conn, 20) is None


async def test_handle_channel_upsert_self_heals_missing_parent_category(db_conn):
    # A mod can create a category and move an existing channel into it in
    # two separate gateway events -- the category may not have a channels
    # row yet when the moved channel's update event arrives.
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    category = FakeGuildChannelForOverwrites(
        id=5, guild=guild, name="New Category", type=discord.ChannelType.category
    )
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild, category=category, name="general")

    await events.handle_channel_upsert(db_conn, channel, guild_id=1)

    assert (await _channel_row(db_conn, 5))["name"] == "New Category"
    assert (await _channel_row(db_conn, 10))["parent_id"] == 5


async def test_handle_channel_create_inserts_with_indexed_false(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild, name="new-channel")

    await events.handle_channel_create(db_conn, channel, guild_id=1)

    row = await _channel_row(db_conn, 10)
    assert row is not None
    assert row["indexed"] is False


async def test_handle_channel_create_computes_is_public(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild, name="new-channel")

    await events.handle_channel_create(db_conn, channel, guild_id=1)

    assert (await _channel_row(db_conn, 10))["is_public"] is True


async def test_handle_channel_create_skips_voice_and_stage_voice(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(
        id=20, guild=guild, type=discord.ChannelType.stage_voice
    )

    await events.handle_channel_create(db_conn, channel, guild_id=1)

    assert await _channel_row(db_conn, 20) is None


async def test_handle_channel_create_self_heals_missing_parent_category(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    category = FakeGuildChannelForOverwrites(
        id=5, guild=guild, name="New Category", type=discord.ChannelType.category
    )
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild, category=category, name="general")

    await events.handle_channel_create(db_conn, channel, guild_id=1)

    assert (await _channel_row(db_conn, 5))["name"] == "New Category"
    assert (await _channel_row(db_conn, 10))["parent_id"] == 5


async def test_handle_channel_create_does_not_reset_indexed_on_a_duplicate_event(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute("UPDATE channels SET indexed = true WHERE id = 10")
    guild = FakeGuild(default_role=FakeRole(BOTH_REQUIRED), channels=[])
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild, name="general")

    await events.handle_channel_create(db_conn, channel, guild_id=1)

    assert (await _channel_row(db_conn, 10))["indexed"] is True


async def test_handle_channel_delete_removes_the_row_and_cascades(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (1, "a"))
    await db_conn.execute(
        "INSERT INTO messages (id, channel_id, author_id, content, posted_at) "
        "VALUES (%s, %s, %s, %s, now())",
        (1000, 10, 1, "hi"),
    )

    await events.handle_channel_delete(db_conn, 10)

    assert await _channel_row(db_conn, 10) is None
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 0


async def test_handle_channel_delete_is_a_no_op_for_unknown_id(db_conn):
    await events.handle_channel_delete(db_conn, 999999)


async def test_handle_channel_delete_uncategorizes_rather_than_deletes_children(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 4, %s)",
        (1, 1, "A Category"),
    )
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, parent_id, type, name) VALUES (%s, %s, %s, 0, %s)",
        (10, 1, 1, "general"),
    )

    await events.handle_channel_delete(db_conn, 1)

    assert await _channel_row(db_conn, 1) is None
    assert (await _channel_row(db_conn, 10))["parent_id"] is None


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


async def test_handle_role_permissions_changed_recomputes_bot_can_read_too(db_conn):
    # The edited/deleted role could be one the bot itself holds -- this
    # loop must recompute bot_can_read alongside is_public, not just the
    # latter.
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, is_public, bot_can_read) "
        "VALUES (%s, 1, 0, %s, true, true)",
        (10, "general"),
    )

    role = FakeRole(0)
    guild = FakeGuild(default_role=role, channels=[])
    channel = FakeGuildChannelForOverwrites(id=10, guild=guild, bot_permissions_value=0)
    guild.channels = [channel]

    await events.handle_role_permissions_changed(db_conn, guild)

    assert await repository.get_channel_bot_can_read(db_conn, 10) is False


async def test_handle_role_permissions_changed_does_not_touch_overwrite_tables(db_conn):
    # A role's own permissions/name/color changing never changes which
    # channels have an overwrite for it -- this recompute loop stays scoped
    # to is_public, guarding against accidentally over-scoping it later to
    # also re-sync overwrites (which handle_channel_permissions_changed
    # already owns, on the events that actually change them).
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, is_public) "
        "VALUES (10, 1, 0, 'general', true)"
    )
    await db_conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (500, 1, 'Mods', 0, 0, 0)"
    )
    await db_conn.execute(
        "INSERT INTO channel_role_overwrites (channel_id, role_id, allow, deny) "
        "VALUES (10, 500, 1024, 2048)"
    )
    role = FakeRole(BOTH_REQUIRED)
    guild = FakeGuild(default_role=role, channels=[])
    guild.channels = [FakeGuildChannelForOverwrites(id=10, guild=guild)]

    await events.handle_role_permissions_changed(db_conn, guild)

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT allow, deny FROM channel_role_overwrites "
            "WHERE channel_id = 10 AND role_id = 500"
        )
        row = await cur.fetchone()
    assert row == {"allow": 1024, "deny": 2048}


@dataclass
class FakeColour:
    value: int


@dataclass
class FakeDiscordRole:
    id: int
    name: str = "a role"
    color: FakeColour = field(default_factory=lambda: FakeColour(value=0))
    position: int = 0
    permissions: FakeColour = field(default_factory=lambda: FakeColour(value=0))


async def test_handle_role_upsert_inserts_a_new_row(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    role = FakeDiscordRole(
        id=111,
        name="Moderators",
        color=FakeColour(value=0xFF0000),
        position=3,
        permissions=FakeColour(value=0x800),
    )

    await events.handle_role_upsert(db_conn, role, guild_id=1)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name, color, position, permissions FROM roles WHERE id = 111")
        row = await cur.fetchone()
    assert row == {"name": "Moderators", "color": 0xFF0000, "position": 3, "permissions": 0x800}


async def test_handle_role_delete_removes_the_row(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    role = FakeDiscordRole(id=111, name="Moderators")
    await events.handle_role_upsert(db_conn, role, guild_id=1)

    await events.handle_role_delete(db_conn, 111)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM roles WHERE id = 111")
        assert await cur.fetchone() is None


async def test_handle_member_update_updates_display_name_in_the_database(db_conn):
    await db_conn.execute(
        "INSERT INTO users (id, display_name, avatar_hash) VALUES (%s, %s, %s)",
        (1, "old-nick", None),
    )
    before = FakeAuthor(id=1, display_name="old-nick")
    after = FakeAuthor(id=1, display_name="new-nick")

    await events.handle_member_update(db_conn, before, after)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT display_name FROM users WHERE id = 1")
        assert (await cur.fetchone())["display_name"] == "new-nick"


async def test_handle_member_update_inserts_a_new_user_row_if_none_existed(db_conn):
    # A member who renames before ever posting -- no prior users row exists.
    before = FakeAuthor(id=2, display_name="old-nick")
    after = FakeAuthor(id=2, display_name="new-nick")

    await events.handle_member_update(db_conn, before, after)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT display_name FROM users WHERE id = 2")
        assert (await cur.fetchone())["display_name"] == "new-nick"


async def test_handle_member_update_is_a_no_op_in_the_database_when_unchanged(db_conn):
    await db_conn.execute(
        "INSERT INTO users (id, display_name, avatar_hash) VALUES (%s, %s, %s)",
        (1, "same-nick", None),
    )
    same = FakeAuthor(id=1, display_name="same-nick")

    await events.handle_member_update(db_conn, same, same)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT display_name FROM users WHERE id = 1")
        assert (await cur.fetchone())["display_name"] == "same-nick"
