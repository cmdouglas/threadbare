"""Aggregate reaction counts -> HTML badges. Pure, no I/O — the (emoji, count)
pairs come straight from db.queries.get_reactions_for_message.
"""

from threadbare.rendering.emoji import render_emoji_token_html


def render_reaction_badges_html(reactions: list[tuple[str, int]]) -> str:
    if not reactions:
        return ""
    badges = "".join(
        f'<span class="reaction-badge">{render_emoji_token_html(emoji)}'
        f'<span class="reaction-count">{count}</span></span>'
        for emoji, count in reactions
    )
    return f'<div class="reactions">{badges}</div>'
