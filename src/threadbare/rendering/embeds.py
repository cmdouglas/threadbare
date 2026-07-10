"""Embed row -> HTML. Pure, no I/O — description text runs back through
rendering.markdown since Discord embeds support the same markdown as regular
message content.
"""

import html

import nh3

from threadbare.rendering import markdown
from threadbare.rendering.markdown import ResolvedRefs

# A superset of markdown.ALLOWED_TAGS/ATTRIBUTES: embeds add their own
# structural wrapper (div/dl/dt/dd) around markdown-rendered description text.
ALLOWED_TAGS = markdown.ALLOWED_TAGS | {"div", "dl", "dt", "dd"}
ALLOWED_ATTRIBUTES = {
    **markdown.ALLOWED_ATTRIBUTES,
    "div": {"class", "style"},
    "dl": {"class"},
}


def render_embed_html(embed_row: dict, *, refs: ResolvedRefs) -> str:
    parts: list[str] = []

    if embed_row.get("author_name"):
        name = html.escape(embed_row["author_name"])
        if embed_row.get("author_url"):
            safe_url = html.escape(embed_row["author_url"], quote=True)
            name = f'<a href="{safe_url}">{name}</a>'
        parts.append(f'<div class="embed-author">{name}</div>')

    if embed_row.get("title"):
        title = html.escape(embed_row["title"])
        if embed_row.get("url"):
            safe_url = html.escape(embed_row["url"], quote=True)
            title = f'<a href="{safe_url}">{title}</a>'
        parts.append(f'<div class="embed-title">{title}</div>')

    if embed_row.get("description"):
        description_html = markdown.render_message_content(embed_row["description"], refs=refs)
        parts.append(f'<div class="embed-description">{description_html}</div>')

    if embed_row.get("fields"):
        field_items = "".join(
            f"<dt>{html.escape(field['name'])}</dt><dd>{html.escape(field['value'])}</dd>"
            for field in embed_row["fields"]
        )
        parts.append(f'<dl class="embed-fields">{field_items}</dl>')

    if embed_row.get("image_url"):
        safe_url = html.escape(embed_row["image_url"], quote=True)
        parts.append(f'<img class="embed-image" src="{safe_url}" alt="">')

    if embed_row.get("thumbnail_url"):
        safe_url = html.escape(embed_row["thumbnail_url"], quote=True)
        parts.append(f'<img class="embed-thumbnail" src="{safe_url}" alt="">')

    if embed_row.get("footer_text"):
        parts.append(f'<div class="embed-footer">{html.escape(embed_row["footer_text"])}</div>')

    style = f' style="--embed-color: #{embed_row["color"]:06x}"' if embed_row.get("color") else ""
    raw_html = f'<div class="embed"{style}>{"".join(parts)}</div>'
    return nh3.clean(raw_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
