"""Discord payload -> row shape. Pure, no I/O — takes MessageLike/UserLike/
AttachmentLike Protocol objects (see discord_types.py) and returns plain
dicts ready for repository.upsert_*.
"""

from datetime import datetime

import discord

from threadbare.sync_worker.discord_types import (
    AttachmentLike,
    EmbedLike,
    MessageLike,
    ThreadLike,
    UserLike,
)


def message_to_row(message: MessageLike, *, channel_id: int | None, thread_id: int | None) -> dict:
    reply_to_id = message.reference.message_id if message.reference else None
    return {
        "id": message.id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "author_id": message.author.id,
        "content": message.content,
        "reply_to_id": reply_to_id,
        "posted_at": message.created_at,
        "edited_at": message.edited_at,
        "flags": 0,
    }


def user_to_row(user: UserLike) -> dict:
    return {
        "id": user.id,
        "display_name": user.display_name,
        "avatar_hash": user.avatar.key if user.avatar else None,
    }


def thread_to_row(thread: ThreadLike) -> dict:
    return {
        "id": thread.id,
        "parent_channel_id": thread.parent_id,
        "name": thread.name,
        "archived": thread.archived,
        "created_at": thread.created_at or discord.utils.snowflake_time(thread.id),
        "message_count": thread.message_count,
    }


def attachment_to_row(
    attachment: AttachmentLike, *, message_id: int, url_expires_at: datetime
) -> dict:
    return {
        "id": attachment.id,
        "message_id": message_id,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size": attachment.size,
        "cached_url": attachment.url,
        "url_expires_at": url_expires_at,
    }


def embed_to_row(embed: EmbedLike, *, message_id: int, position: int) -> dict:
    # On a real discord.py Embed, footer/image/thumbnail/author are never
    # None themselves (always an EmbedProxy, falsy when empty) — the `if
    # embed.footer else None` guards exist for the sub-field access, and
    # incidentally also tolerate simple test fakes that set these to None
    # directly instead of a nested fake object.
    return {
        "message_id": message_id,
        "position": position,
        "type": embed.type,
        "title": embed.title,
        "description": embed.description,
        "url": embed.url,
        "color": embed.color.value if embed.color else None,
        "author_name": embed.author.name if embed.author else None,
        "author_url": embed.author.url if embed.author else None,
        "footer_text": embed.footer.text if embed.footer else None,
        "image_url": embed.image.url if embed.image else None,
        "thumbnail_url": embed.thumbnail.url if embed.thumbnail else None,
        "fields": [{"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields],
    }
