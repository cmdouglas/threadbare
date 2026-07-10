"""Reply-chain quoting, rendered as a classic forum quote block. One-hop
only, matching both Discord's own reply-preview UX and messages.reply_to_id's
actual capability (a single self-referencing FK, not a chain).
"""

import html

import psycopg

from threadbare import urls
from threadbare.db import queries
from threadbare.pagination import page_number_for_offset

DEFAULT_SNIPPET_LIMIT = 280


def truncate_snippet(text: str, limit: int = DEFAULT_SNIPPET_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


async def render_reply_quote(conn: psycopg.AsyncConnection, message_row: dict) -> str | None:
    reply_to_id = message_row.get("reply_to_id")
    if reply_to_id is None:
        return None

    # reply_to_id is ON DELETE SET NULL, so this only returns None for a
    # target this instance never indexed in the first place (outside
    # reconciliation's lookback, or never backfilled) -- not a bug.
    target = await queries.get_message_for_render(conn, reply_to_id)
    if target is None:
        return None

    preceding = await queries.count_messages_before(
        conn,
        thread_id=target["thread_id"],
        channel_id=target["channel_id"],
        before=(target["posted_at"], target["id"]),
    )
    page = page_number_for_offset(preceding)
    href = html.escape(urls.permalink_for_message(target, page=page), quote=True)

    author = html.escape(target["author_display_name"])
    snippet = html.escape(truncate_snippet(target["content"]))
    return (
        f'<blockquote class="reply-quote" data-quoted-message-id="{reply_to_id}">'
        f'<a href="{href}">'
        f'<span class="reply-quote-author">{author}</span></a> '
        f'<span class="reply-quote-snippet">{snippet}</span>'
        "</blockquote>"
    )
