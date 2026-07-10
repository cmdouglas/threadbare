"""Attachment row -> HTML. Pure, no I/O. Discord flags a spoiler attachment
client-side by prefixing the filename with SPOILER_ (no separate schema
column) — parseable from attachments.filename alone.
"""

import html

SPOILER_PREFIX = "SPOILER_"


def is_spoiler_attachment(filename: str) -> bool:
    return filename.startswith(SPOILER_PREFIX)


def display_filename(filename: str) -> str:
    if is_spoiler_attachment(filename):
        return filename[len(SPOILER_PREFIX) :]
    return filename


def render_attachment_html(row: dict) -> str:
    is_image = (row["content_type"] or "").startswith("image/")
    safe_filename = html.escape(display_filename(row["filename"]))
    safe_url = html.escape(row["cached_url"], quote=True)

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
