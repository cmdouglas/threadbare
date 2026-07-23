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


class AssetLike(Protocol):
    key: str


class UserLike(Protocol):
    id: int
    display_name: str
    # discord.py's Member/User expose the avatar hash via an Asset object
    # (.avatar.key), not a plain string attribute — this was wrong before
    # and broke on first contact with a real discord.py object; verified
    # against the real API now (see tests/live_discord).
    avatar: AssetLike | None


class AttachmentLike(Protocol):
    id: int
    filename: str
    content_type: str | None
    size: int
    url: str


class ReactionLike(Protocol):
    # discord.py's Reaction.emoji is Emoji | PartialEmoji | str — typed as
    # object here (not a discord.py Union) since the only operation ever
    # performed on it is str(), and this module stays discord.py-free
    # elsewhere too.
    emoji: object
    count: int


class MessageReferenceLike(Protocol):
    message_id: int | None


class EmbedFooterLike(Protocol):
    text: str | None


class EmbedMediaLike(Protocol):
    url: str | None


class EmbedAuthorLike(Protocol):
    name: str | None
    url: str | None


class EmbedFieldLike(Protocol):
    name: str
    value: str
    inline: bool


class EmbedLike(Protocol):
    # discord.py's Embed uses its own EmbedProxy/_EmptyEmbed sentinel for
    # unset sub-objects rather than None, but every attribute access on one
    # of those proxies (e.g. embed.footer.text) itself yields another
    # sentinel rather than raising — so a plain `embed.footer is not None`
    # check works fine, matching how transform.py reads these.
    type: str | None
    title: str | None
    description: str | None
    url: str | None
    # discord.py's Embed.color is a Colour object (.value: int) or None.
    color: object | None
    author: EmbedAuthorLike | None
    footer: EmbedFooterLike | None
    image: EmbedMediaLike | None
    thumbnail: EmbedMediaLike | None
    video: EmbedMediaLike | None
    fields: list[EmbedFieldLike]


class MessageLike(Protocol):
    id: int
    author: UserLike
    content: str
    created_at: datetime
    edited_at: datetime | None
    # discord.py's MessageType enum; only .value (int) is read. Real
    # discord.Message.type is always populated (confirmed identical parsing
    # for both gateway and REST/history payloads, message.py:2209) -- test
    # fakes may omit it (see transform.message_to_row's getattr fallback).
    type: object
    reference: MessageReferenceLike | None
    attachments: list[AttachmentLike]
    reactions: list[ReactionLike]
    embeds: list[EmbedLike]


class ThreadLike(Protocol):
    id: int
    parent_id: int
    name: str
    archived: bool
    # None for threads created before Discord introduced this field
    # (2022-01-09) — discord.py leaves it unset rather than backfilling it.
    created_at: datetime | None
    message_count: int
