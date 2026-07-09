"""Narrow structural types describing only the attributes our code reads off
discord.py objects. Business logic is written against these Protocols, never
against discord.py types directly, so it can be unit tested with plain
fixtures (SimpleNamespace, dataclasses) instead of a live gateway connection.
"""

from datetime import datetime
from typing import Protocol


class OverwriteLike(Protocol):
    allow: int
    deny: int


class UserLike(Protocol):
    id: int
    display_name: str
    avatar_key: str | None


class AttachmentLike(Protocol):
    id: int
    filename: str
    content_type: str | None
    size: int
    url: str


class MessageReferenceLike(Protocol):
    message_id: int | None


class MessageLike(Protocol):
    id: int
    author: UserLike
    content: str
    created_at: datetime
    edited_at: datetime | None
    reference: MessageReferenceLike | None
    attachments: list[AttachmentLike]
