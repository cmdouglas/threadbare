"""Turns a page's message rows into the shape every post-rendering template
consumes: a list of *posts*, each a list of (row, rendered) segments.

Always a list of lists, even when consecutive-post merging is off -- then
every post simply has exactly one segment. One uniform shape means the
templates have no merged/unmerged branch, and `_post.html` keeps emitting
byte-identical markup for a single-segment post either way.
"""

from threadbare import post_groups
from threadbare.rendering.render_service import render_message_for_display


async def render_posts(conn, rows: list[dict], *, script_root: str, page_size: int, merged: bool):
    """`merged` must match the flag the rows were fetched with: grouping reads
    each row's starts_group, which only the merged query selects.
    """
    groups = post_groups.group_messages(rows) if merged else [[row] for row in rows]
    return [
        [
            (
                row,
                await render_message_for_display(
                    conn, row, script_root=script_root, page_size=page_size, merged=merged
                ),
            )
            for row in group
        ]
        for group in groups
    ]
