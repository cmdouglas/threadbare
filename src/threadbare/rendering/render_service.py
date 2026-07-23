"""Orchestration entry point for rendering one message: ties together
markdown parsing, id resolution, reply quoting, and attachment/embed/reaction
rendering. This is what the forum web app (ROADMAP.md §4) calls per message.
"""

from dataclasses import dataclass

import psycopg

from threadbare.db import queries
from threadbare.rendering.attachments import render_attachment_html
from threadbare.rendering.embeds import render_embed_html
from threadbare.rendering.markdown import (
    ResolvedRefs,
    collect_referenced_ids,
    render_message_content,
)
from threadbare.rendering.quotes import render_reply_quote
from threadbare.rendering.reactions import render_reaction_badges_html
from threadbare.rendering.resolve import build_resolved_refs
from threadbare.rendering.system_messages import is_system_message_type, render_system_message_text


@dataclass(frozen=True)
class RenderedMessage:
    content_html: str
    reply_quote_html: str | None
    attachments_html: str
    embeds_html: str
    reactions_html: str
    is_system_message: bool = False


async def render_message_for_display(
    conn: psycopg.AsyncConnection, message_row: dict, *, script_root: str = ""
) -> RenderedMessage:
    message_type = message_row.get("type", 0)

    if is_system_message_type(message_type):
        system_text = render_system_message_text(
            message_type,
            content=message_row["content"],
            author_display_name=message_row["author_display_name"],
            posted_at=message_row["posted_at"],
        )
        content_html = render_message_content(
            system_text, refs=ResolvedRefs(users={}, channels={})
        )
        return RenderedMessage(
            content_html=content_html,
            reply_quote_html=None,
            attachments_html="",
            embeds_html="",
            reactions_html="",
            is_system_message=True,
        )

    message_id = message_row["id"]

    referenced_ids = collect_referenced_ids(message_row["content"])
    refs = await build_resolved_refs(conn, referenced_ids)

    content_html = render_message_content(message_row["content"], refs=refs)
    reply_quote_html = await render_reply_quote(conn, message_row, script_root=script_root)

    attachments = await queries.get_attachments_for_message(conn, message_id)
    attachments_html = "".join(
        render_attachment_html(row, script_root=script_root) for row in attachments
    )

    embeds = await queries.get_embeds_for_message(conn, message_id)
    embeds_html = "".join(render_embed_html(row, refs=refs) for row in embeds)

    reactions = await queries.get_reactions_for_message(conn, message_id)
    reactions_html = render_reaction_badges_html(reactions)

    return RenderedMessage(
        content_html=content_html,
        reply_quote_html=reply_quote_html,
        attachments_html=attachments_html,
        embeds_html=embeds_html,
        reactions_html=reactions_html,
    )
