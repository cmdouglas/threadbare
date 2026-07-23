from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord

from threadbare.sync_worker.transform import (
    attachment_to_row,
    embed_to_row,
    message_to_row,
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
    fields: list = field(default_factory=list)


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

    assert user_to_row(user) == {"id": 1, "display_name": "alice", "avatar_hash": "abc123"}


def test_user_to_row_handles_no_avatar():
    user = FakeUser(id=1, display_name="alice", avatar=None)

    assert user_to_row(user)["avatar_hash"] is None


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


def test_thread_to_row_falls_back_to_snowflake_time_when_created_at_is_none():
    # discord.py leaves created_at as None for threads created before
    # 2022-01-09; the threads.created_at column is NOT NULL, so we derive it
    # from the id (a Discord snowflake always encodes its own creation time).
    thread = FakeThread(id=99, parent_id=10, name="old thread", created_at=None)

    row = thread_to_row(thread)

    assert row["created_at"] == discord.utils.snowflake_time(99)
