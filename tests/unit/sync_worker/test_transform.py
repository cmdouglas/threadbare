from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord

from threadbare.sync_worker.transform import (
    attachment_to_row,
    channel_overwrite_rows,
    channel_to_row,
    embed_to_row,
    message_to_row,
    role_to_row,
    thread_to_row,
    user_to_row,
)


@dataclass
class FakeAsset:
    key: str


@dataclass
class FakeUser:
    id: int
    display_name: str
    avatar: FakeAsset | None = None
    bot: bool = False


@dataclass
class FakeGuild:
    id: int


@dataclass
class FakeRole:
    id: int
    name: str = "a role"
    color: object = None
    position: int = 0
    permissions: object = field(default_factory=lambda: FakeColour(value=0))


@dataclass
class FakeMember:
    id: int
    display_name: str
    guild: FakeGuild
    roles: list = field(default_factory=list)
    avatar: FakeAsset | None = None
    bot: bool = False


@dataclass
class FakeAttachment:
    id: int
    filename: str
    size: int
    url: str
    content_type: str | None = None


@dataclass
class FakeReference:
    message_id: int | None


@dataclass
class FakeMessage:
    id: int
    author: FakeUser
    content: str
    created_at: datetime
    edited_at: datetime | None = None
    reference: FakeReference | None = None
    attachments: list = field(default_factory=list)
    type: object | None = None


@dataclass
class FakeColour:
    value: int


@dataclass
class FakeEmbedFooter:
    text: str | None = None


@dataclass
class FakeEmbedMedia:
    url: str | None = None


@dataclass
class FakeEmbedAuthor:
    name: str | None = None
    url: str | None = None


@dataclass
class FakeEmbedField:
    name: str
    value: str
    inline: bool = False


@dataclass
class FakeEmbed:
    type: str | None = None
    title: str | None = None
    description: str | None = None
    url: str | None = None
    color: FakeColour | None = None
    author: FakeEmbedAuthor | None = None
    footer: FakeEmbedFooter | None = None
    image: FakeEmbedMedia | None = None
    thumbnail: FakeEmbedMedia | None = None
    video: FakeEmbedMedia | None = None
    fields: list = field(default_factory=list)


@dataclass
class FakeChannelType:
    value: int


@dataclass
class FakeChannel:
    id: int
    category_id: int | None
    type: FakeChannelType
    name: str
    position: int = 0
    topic: str | None = None


@dataclass
class FakeCategoryChannel:
    # No `topic` field at all -- a real discord.py CategoryChannel has no
    # such attribute (unlike TextChannel/ForumChannel), so this exercises
    # channel_to_row's getattr(channel, "topic", None) fallback for real,
    # not just a field defaulted to None.
    id: int
    category_id: None
    type: FakeChannelType
    name: str
    position: int = 0


@dataclass
class FakeThread:
    id: int
    parent_id: int
    name: str
    archived: bool = False
    created_at: datetime | None = None
    message_count: int = 0


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_message_to_row_maps_basic_fields_for_a_channel_message():
    author = FakeUser(id=1, display_name="alice")
    message = FakeMessage(id=100, author=author, content="hi", created_at=NOW)

    row = message_to_row(message, channel_id=10, thread_id=None)

    assert row == {
        "id": 100,
        "channel_id": 10,
        "thread_id": None,
        "author_id": 1,
        "content": "hi",
        "reply_to_id": None,
        "posted_at": NOW,
        "edited_at": None,
        "flags": 0,
        "type": 0,
    }


def test_message_to_row_for_a_thread_message():
    author = FakeUser(id=1, display_name="alice")
    message = FakeMessage(id=100, author=author, content="hi", created_at=NOW)

    row = message_to_row(message, channel_id=None, thread_id=99)

    assert row["channel_id"] is None
    assert row["thread_id"] == 99


def test_message_to_row_captures_reply_reference():
    author = FakeUser(id=1, display_name="alice")
    message = FakeMessage(
        id=100,
        author=author,
        content="hi",
        created_at=NOW,
        reference=FakeReference(message_id=42),
    )

    row = message_to_row(message, channel_id=10, thread_id=None)

    assert row["reply_to_id"] == 42


def test_message_to_row_handles_reference_with_no_message_id():
    # e.g. a reference to a message in another (uncached) channel/thread
    author = FakeUser(id=1, display_name="alice")
    message = FakeMessage(
        id=100,
        author=author,
        content="hi",
        created_at=NOW,
        reference=FakeReference(message_id=None),
    )

    row = message_to_row(message, channel_id=10, thread_id=None)

    assert row["reply_to_id"] is None


def test_message_to_row_captures_message_type():
    author = FakeUser(id=1, display_name="alice")
    message = FakeMessage(
        id=100, author=author, content="", created_at=NOW, type=discord.MessageType.new_member
    )

    row = message_to_row(message, channel_id=10, thread_id=None)

    assert row["type"] == discord.MessageType.new_member.value


def test_message_to_row_defaults_type_to_zero_when_absent():
    author = FakeUser(id=1, display_name="alice")
    message = FakeMessage(id=100, author=author, content="hi", created_at=NOW)

    row = message_to_row(message, channel_id=10, thread_id=None)

    assert row["type"] == 0


def test_message_to_row_captures_edited_at():
    author = FakeUser(id=1, display_name="alice")
    edited = datetime(2026, 1, 2, tzinfo=UTC)
    message = FakeMessage(id=100, author=author, content="hi", created_at=NOW, edited_at=edited)

    row = message_to_row(message, channel_id=10, thread_id=None)

    assert row["edited_at"] == edited


def test_user_to_row():
    user = FakeUser(id=1, display_name="alice", avatar=FakeAsset(key="abc123"))

    assert user_to_row(user) == {
        "id": 1,
        "display_name": "alice",
        "avatar_hash": "abc123",
        "is_bot": False,
        "role_ids": [],
    }


def test_user_to_row_handles_no_avatar():
    user = FakeUser(id=1, display_name="alice", avatar=None)

    assert user_to_row(user)["avatar_hash"] is None


def test_user_to_row_bot_flag():
    user = FakeUser(id=1, display_name="a-bot", bot=True)

    assert user_to_row(user)["is_bot"] is True


def test_user_to_row_extracts_role_ids_excluding_everyone():
    guild = FakeGuild(id=999)
    member = FakeMember(
        id=1,
        display_name="alice",
        guild=guild,
        roles=[FakeRole(id=999), FakeRole(id=111), FakeRole(id=222)],
    )

    assert user_to_row(member)["role_ids"] == [111, 222]


def test_user_to_row_role_ids_empty_for_bare_user_without_roles():
    # A bare discord.User (webhook-posted messages) has no .roles/.guild at
    # all -- must not raise, just report no roles.
    user = FakeUser(id=1, display_name="a-webhook", bot=True)

    assert user_to_row(user)["role_ids"] == []


def test_attachment_to_row():
    attachment = FakeAttachment(
        id=200,
        filename="cat.png",
        size=1024,
        url="https://cdn.example/cat.png",
        content_type="image/png",
    )
    expires_at = datetime(2026, 1, 2, tzinfo=UTC)

    row = attachment_to_row(attachment, message_id=100, url_expires_at=expires_at)

    assert row == {
        "id": 200,
        "message_id": 100,
        "filename": "cat.png",
        "content_type": "image/png",
        "size": 1024,
        "cached_url": "https://cdn.example/cat.png",
        "url_expires_at": expires_at,
    }


def test_embed_to_row_maps_basic_fields():
    embed = FakeEmbed(
        type="rich",
        title="A link preview",
        description="some *markdown* text",
        url="https://example.com",
        color=FakeColour(value=0x00FF00),
        author=FakeEmbedAuthor(name="alice", url="https://example.com/alice"),
        footer=FakeEmbedFooter(text="a footer"),
        image=FakeEmbedMedia(url="https://example.com/image.png"),
        thumbnail=FakeEmbedMedia(url="https://example.com/thumb.png"),
        video=FakeEmbedMedia(url="https://example.com/video.mp4"),
        fields=[FakeEmbedField(name="k", value="v", inline=True)],
    )

    row = embed_to_row(embed, message_id=100, position=0)

    assert row == {
        "message_id": 100,
        "position": 0,
        "type": "rich",
        "title": "A link preview",
        "description": "some *markdown* text",
        "url": "https://example.com",
        "color": 0x00FF00,
        "author_name": "alice",
        "author_url": "https://example.com/alice",
        "footer_text": "a footer",
        "image_url": "https://example.com/image.png",
        "thumbnail_url": "https://example.com/thumb.png",
        "video_url": "https://example.com/video.mp4",
        "fields": [{"name": "k", "value": "v", "inline": True}],
    }


def test_embed_to_row_handles_missing_optional_fields():
    embed = FakeEmbed()

    row = embed_to_row(embed, message_id=100, position=1)

    assert row == {
        "message_id": 100,
        "position": 1,
        "type": None,
        "title": None,
        "description": None,
        "url": None,
        "color": None,
        "author_name": None,
        "author_url": None,
        "footer_text": None,
        "image_url": None,
        "thumbnail_url": None,
        "video_url": None,
        "fields": [],
    }


def test_embed_to_row_serializes_multiple_fields_in_order():
    embed = FakeEmbed(
        fields=[
            FakeEmbedField(name="first", value="1"),
            FakeEmbedField(name="second", value="2", inline=True),
        ]
    )

    row = embed_to_row(embed, message_id=100, position=0)

    assert row["fields"] == [
        {"name": "first", "value": "1", "inline": False},
        {"name": "second", "value": "2", "inline": True},
    ]


def test_channel_to_row_maps_basic_fields():
    channel = FakeChannel(
        id=10, category_id=1, type=FakeChannelType(0), name="general", position=2, topic="chat here"
    )

    row = channel_to_row(channel, guild_id=999)

    assert row == {
        "id": 10,
        "guild_id": 999,
        "parent_id": 1,
        "type": 0,
        "name": "general",
        "position": 2,
        "topic": "chat here",
    }


def test_channel_to_row_topic_is_none_when_attribute_is_absent():
    category = FakeCategoryChannel(
        id=1, category_id=None, type=FakeChannelType(4), name="A Category"
    )

    row = channel_to_row(category, guild_id=999)

    assert row["topic"] is None
    assert row["parent_id"] is None


def test_thread_to_row_maps_basic_fields():
    thread = FakeThread(id=99, parent_id=10, name="general chat", archived=True, created_at=NOW)

    row = thread_to_row(thread)

    assert row == {
        "id": 99,
        "parent_channel_id": 10,
        "name": "general chat",
        "archived": True,
        "created_at": NOW,
        "message_count": 0,
    }


def test_role_to_row():
    role = FakeRole(
        id=111,
        name="Moderators",
        color=FakeColour(value=0xFF0000),
        position=3,
        permissions=FakeColour(value=0x800),
    )

    row = role_to_row(role, guild_id=999)

    assert row == {
        "id": 111,
        "guild_id": 999,
        "name": "Moderators",
        "color": 0xFF0000,
        "position": 3,
        "permissions": 0x800,
    }


class FakeOverwriteTargetRole(discord.Role):
    """Subclasses the real discord.Role for isinstance purposes -- a plain
    duck-typed fake would defeat the exact isinstance(target, discord.Role)
    branch channel_overwrite_rows relies on to split role- vs. member-tier
    overwrites, same reasoning as FakeForumChannel elsewhere in this
    project.
    """

    def __init__(self, id):
        self.id = id


@dataclass(eq=False)
class FakeOverwriteTargetMember:
    id: int


@dataclass
class FakePermissionPair:
    allow: FakeColour
    deny: FakeColour

    def pair(self):
        return self.allow, self.deny


def test_channel_overwrite_rows_splits_role_and_member_targets():
    role = FakeOverwriteTargetRole(id=111)
    member = FakeOverwriteTargetMember(id=222)
    overwrites = {
        role: FakePermissionPair(FakeColour(value=0x400), FakeColour(value=0x800)),
        member: FakePermissionPair(FakeColour(value=0x1), FakeColour(value=0x2)),
    }

    role_rows, member_rows = channel_overwrite_rows(10, overwrites)

    assert role_rows == [{"channel_id": 10, "role_id": 111, "allow": 0x400, "deny": 0x800}]
    assert member_rows == [{"channel_id": 10, "user_id": 222, "allow": 0x1, "deny": 0x2}]


def test_channel_overwrite_rows_returns_empty_lists_for_no_overwrites():
    assert channel_overwrite_rows(10, {}) == ([], [])


def test_thread_to_row_falls_back_to_snowflake_time_when_created_at_is_none():
    # discord.py leaves created_at as None for threads created before
    # 2022-01-09; the threads.created_at column is NOT NULL, so we derive it
    # from the id (a Discord snowflake always encodes its own creation time).
    thread = FakeThread(id=99, parent_id=10, name="old thread", created_at=None)

    row = thread_to_row(thread)

    assert row["created_at"] == discord.utils.snowflake_time(99)
