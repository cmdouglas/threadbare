"""Attachment row -> HTML. Pure, no I/O. Discord flags a spoiler attachment
client-side by prefixing the filename with SPOILER_ (no separate schema
column) — parseable from attachments.filename alone.
"""

import html

from threadbare import urls

SPOILER_PREFIX = "SPOILER_"


def is_spoiler_attachment(filename: str) -> bool:
    return filename.startswith(SPOILER_PREFIX)


def display_filename(filename: str) -> str:
    if is_spoiler_attachment(filename):
        return filename[len(SPOILER_PREFIX) :]
    return filename


def render_attachment_html(row: dict, *, script_root: str = "") -> str:
    is_image = (row["content_type"] or "").startswith("image/")
    safe_filename = html.escape(display_filename(row["filename"]))
    # Routed through the /att/{id} proxy, not row["cached_url"] directly --
    # Discord's signed CDN URLs expire (~24h) and are frequently already
    # dead by the time a page is viewed; the proxy refreshes on demand.
    # script_root (request.script_root under a subpath deployment, "" at
    # root) is prepended here rather than in urls.py itself, which must stay
    # importable outside a Flask request context (see urls.py's docstring).
    safe_url = html.escape(script_root + urls.attachment_proxy_url(row["id"]), quote=True)

    if is_image:
        inner = (
            f'<a class="attachment attachment-image" href="{safe_url}">'
            f'<img src="{safe_url}" alt="{safe_filename}"></a>'
        )
    else:
        inner = f'<a class="attachment attachment-file" href="{safe_url}">{safe_filename}</a>'

    if is_spoiler_attachment(row["filename"]):
        return f'<details class="spoiler"><summary>Spoiler</summary>{inner}</details>'
    return inner
