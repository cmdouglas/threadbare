"""Custom/unicode emoji -> HTML. Pure, no I/O. Discord's emoji CDN URLs are
static and unsigned (unlike attachments' signed, expiring cached_url) — no
proxy or expiry handling needed here.
"""

import html
import re

import emoji as _emoji_lib

CUSTOM_EMOJI_TOKEN_RE = re.compile(r"^<(?P<animated>a)?:(?P<name>\w+):(?P<id>\d+)>$")


def unicode_emoji_title(glyph: str) -> str | None:
    """The `:shortcode:` Discord's own client shows on hover for a standard
    unicode emoji, or None if `glyph` isn't a single recognized emoji. The
    `emoji` package's data is keyed by the same Unicode CLDR annotations
    Discord's naming derives from, so its short "alias" (when one exists,
    e.g. ":smirk:") usually matches Discord's own name more closely than its
    longer canonical "en" name (e.g. ":smirking_face:") -- prefer alias,
    falling back to the canonical name when there's no alias at all.
    """
    data = _emoji_lib.EMOJI_DATA.get(glyph)
    if data is None:
        return None
    aliases = data.get("alias")
    return aliases[0] if aliases else data.get("en")


def render_unicode_text_with_emoji_titles(text: str) -> str:
    """Wraps each recognized unicode emoji substring of `text` in a
    `<span title=":shortcode:">`, leaving the rest as plain escaped text --
    matching Discord's own client, which shows the shortcode on hover for a
    standard emoji, not just custom server emoji.
    """
    matches = _emoji_lib.emoji_list(text)
    if not matches:
        return html.escape(text)
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(html.escape(text[cursor : match["match_start"]]))
        glyph = match["emoji"]
        safe_glyph = html.escape(glyph)
        title = unicode_emoji_title(glyph)
        if title is None:
            parts.append(safe_glyph)
        else:
            parts.append(f'<span title="{html.escape(title)}">{safe_glyph}</span>')
        cursor = match["match_end"]
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def resolve_shortcode_to_unicode(name: str) -> str | None:
    """Converts a bare `:name:` shortcode (no surrounding `<...>` markup,
    just the name) back to its real unicode glyph, or None if `name` isn't a
    recognized emoji. Needed for `NodeType.EMOJI_UNICODE_ENCODED` in
    markdown.py: Discord's own client resolves a `:name:` shortcode to real
    unicode client-side before sending, but bots/webhooks bypass that and
    send the literal text -- Discord's client still renders these visually
    as the real emoji (confirmed against a live example), so this project
    should too rather than showing literal shortcode text.
    """
    token = f":{name}:"
    resolved = _emoji_lib.emojize(token, language="alias")
    return resolved if resolved != token else None


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
    glyph for a standard emoji. A token that matches neither shape, or a
    unicode glyph this project has no shortcode data for, is escaped and
    rendered as literal text rather than raising — it ultimately comes from
    Discord's own payload, not something we control.
    """
    match = CUSTOM_EMOJI_TOKEN_RE.match(token)
    if match is not None:
        return render_custom_emoji_html(
            emoji_id=int(match.group("id")),
            name=match.group("name"),
            animated=match.group("animated") is not None,
        )
    safe_token = html.escape(token)
    title = unicode_emoji_title(token)
    if title is None:
        return safe_token
    return f'<span title="{html.escape(title)}">{safe_token}</span>'
