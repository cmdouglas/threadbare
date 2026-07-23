"""Embed row -> HTML. Pure, no I/O — description text runs back through
rendering.markdown since Discord embeds support the same markdown as regular
message content.
"""

import html

import nh3

from threadbare.rendering import markdown
from threadbare.rendering.markdown import ResolvedRefs

# A superset of markdown.ALLOWED_TAGS/ATTRIBUTES: embeds add their own
# structural wrapper (div/dl/dt/dd) around markdown-rendered description text,
# plus <video> for gifv/video-type embeds (see render_embed_html below).
ALLOWED_TAGS = markdown.ALLOWED_TAGS | {"div", "dl", "dt", "dd", "video"}
ALLOWED_ATTRIBUTES = {
    **markdown.ALLOWED_ATTRIBUTES,
    "div": {"class", "style"},
    "dl": {"class"},
    "video": {"class", "src", "autoplay", "loop", "muted", "playsinline"},
}


def _thumbnail_css_class(embed_type: str | None) -> str:
    """Discord only shrinks a thumbnail to a small floated accent for "rich"
    (bot-crafted) embeds with real body content beside it. A bare
    auto-unfurl link preview (type "link"/"article"/etc., or missing type
    on edge cases) has no such content, so Discord renders its lone
    thumbnail large -- visually identical to embed-image, so reuse that
    class rather than inventing a new one.
    """
    return "embed-thumbnail" if embed_type == "rich" else "embed-image"


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

    has_video = bool(embed_row.get("video_url"))

    if has_video:
        # gifv/video-type unfurls (e.g. Tenor/Giphy): image/thumbnail are
        # just a static preview frame of this same clip, so the video
        # replaces both rather than rendering alongside either.
        safe_url = html.escape(embed_row["video_url"], quote=True)
        parts.append(
            f'<video class="embed-video" src="{safe_url}" autoplay loop muted playsinline></video>'
        )
    elif embed_row.get("image_url"):
        safe_url = html.escape(embed_row["image_url"], quote=True)
        parts.append(f'<img class="embed-image" src="{safe_url}" alt="">')

    if not has_video and embed_row.get("thumbnail_url"):
        safe_url = html.escape(embed_row["thumbnail_url"], quote=True)
        thumb_class = _thumbnail_css_class(embed_row.get("type"))
        parts.append(f'<img class="{thumb_class}" src="{safe_url}" alt="">')

    if embed_row.get("footer_text"):
        parts.append(f'<div class="embed-footer">{html.escape(embed_row["footer_text"])}</div>')

    style = f' style="--embed-color: #{embed_row["color"]:06x}"' if embed_row.get("color") else ""
    raw_html = f'<div class="embed"{style}>{"".join(parts)}</div>'
    return nh3.clean(raw_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)
