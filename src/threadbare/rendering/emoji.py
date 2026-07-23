"""Custom/unicode emoji -> HTML. Pure, no I/O. Discord's emoji CDN URLs are
static and unsigned (unlike attachments' signed, expiring cached_url) — no
proxy or expiry handling needed here.
"""

import html
import re

CUSTOM_EMOJI_TOKEN_RE = re.compile(r"^<(?P<animated>a)?:(?P<name>\w+):(?P<id>\d+)>$")


def render_custom_emoji_html(*, emoji_id: int, name: str, animated: bool) -> str:
    ext = "gif" if animated else "png"
    safe_name = html.escape(name)
    # title (not just alt) is what browsers show as a hover tooltip once the
    # image has actually loaded -- alt text alone only shows on load failure.
    return (
        f'<img class="emoji" src="https://cdn.discordapp.com/emojis/{emoji_id}.{ext}" '
        f'alt=":{safe_name}:" title=":{safe_name}:">'
    )


def render_emoji_token_html(token: str) -> str:
    """Renders the `reactions.emoji` string form: str(discord.PartialEmoji)
    for a custom emoji (`<:name:id>` / `<a:name:id>`), or a bare unicode
    glyph for a standard emoji. A token that matches neither shape is
    escaped and rendered as literal text rather than raising — it
    ultimately comes from Discord's own payload, not something we control.
    """
    match = CUSTOM_EMOJI_TOKEN_RE.match(token)
    if match is None:
        return html.escape(token)
    return render_custom_emoji_html(
        emoji_id=int(match.group("id")),
        name=match.group("name"),
        animated=match.group("animated") is not None,
    )
