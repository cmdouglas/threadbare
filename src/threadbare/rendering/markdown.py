"""Discord-flavored markdown -> HTML. Pure, no I/O — mention/channel ids in
`content` are resolved against a plain ResolvedRefs dict handed in by the
caller (see rendering/resolve.py for the DB-touching lookup that builds one),
so this module never talks to Postgres itself.

Parsing leans on discord-markdown-ast-parser (DESIGN.md's "accept ~80%
fidelity, lean on an existing library" trade-off) rather than a hand-rolled
parser. nh3 is a final defense-in-depth sanitization pass over the HTML we
construct ourselves — every fragment is already built from escaped/allowlisted
pieces, so this mainly guards against a bug in our own node handling turning
into stored XSS across every mirrored message.
"""

import html
import re
from dataclasses import dataclass, field

import nh3
from discord_markdown_ast_parser import parse
from discord_markdown_ast_parser.parser import Node, NodeType

from threadbare.rendering.emoji import (
    render_custom_emoji_html,
    render_unicode_text_with_emoji_titles,
    resolve_shortcode_to_unicode,
)

ALLOWED_TAGS = {
    "strong",
    "em",
    "u",
    "s",
    "span",
    "details",
    "summary",
    "code",
    "pre",
    "blockquote",
    "a",
    "img",
}
ALLOWED_ATTRIBUTES = {
    "span": {"class", "data-channel-id", "title"},
    "a": {"href"},
    "img": {"class", "src", "alt", "title"},
    "code": {"class"},
    "details": {"class"},
}

# discord-markdown-ast-parser's lexer only matches the static custom-emoji
# form (`<:name:id>`) — its EMOJI_CUSTOM regex has no `a` branch at all
# (confirmed by reading lexer.py), so `<a:name:id>` falls through as broken
# text plus a bogus EMOJI_UNICODE_ENCODED node instead of a real emoji.
# Normalizing the `a:` prefix away before parsing avoids that garbled output;
# the animated bit is recovered separately by scanning the raw content for
# animated-emoji ids before normalizing (see _find_animated_emoji_ids) rather
# than losing it, so the emoji still renders as its real animated .gif.
_ANIMATED_EMOJI_RE = re.compile(r"<a(:[a-zA-Z0-9_]{2,}:[0-9]+>)")
_ANIMATED_EMOJI_ID_RE = re.compile(r"<a:[a-zA-Z0-9_]{2,}:([0-9]+)>")


@dataclass(frozen=True)
class ReferencedIds:
    user_ids: frozenset[int]
    role_ids: frozenset[int]
    channel_ids: frozenset[int]


@dataclass(frozen=True)
class ResolvedRefs:
    users: dict[int, str]
    channels: dict[int, str]
    roles: dict[int, str] = field(default_factory=dict)


def _normalize_animated_emoji(content: str) -> str:
    return _ANIMATED_EMOJI_RE.sub(lambda m: f"<{m.group(1)}", content)


def _find_animated_emoji_ids(content: str) -> frozenset[int]:
    return frozenset(int(m.group(1)) for m in _ANIMATED_EMOJI_ID_RE.finditer(content))


def collect_referenced_ids(content: str) -> ReferencedIds:
    user_ids: set[int] = set()
    role_ids: set[int] = set()
    channel_ids: set[int] = set()

    def walk(node: Node) -> None:
        if node.node_type is NodeType.USER:
            user_ids.add(node.discord_id)
        elif node.node_type is NodeType.ROLE:
            role_ids.add(node.discord_id)
        elif node.node_type is NodeType.CHANNEL:
            channel_ids.add(node.discord_id)
        for child in node.children or []:
            walk(child)

    for node in parse(_normalize_animated_emoji(content)):
        walk(node)

    return ReferencedIds(
        user_ids=frozenset(user_ids),
        role_ids=frozenset(role_ids),
        channel_ids=frozenset(channel_ids),
    )


def render_message_content(content: str, *, refs: ResolvedRefs) -> str:
    animated_emoji_ids = _find_animated_emoji_ids(content)
    nodes = parse(_normalize_animated_emoji(content))
    raw_html = "".join(_render_node(node, refs, animated_emoji_ids) for node in nodes)
    return nh3.clean(raw_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES)


def _render_children(node: Node, refs: ResolvedRefs, animated_emoji_ids: frozenset[int]) -> str:
    return "".join(_render_node(child, refs, animated_emoji_ids) for child in node.children or [])


def _render_node(node: Node, refs: ResolvedRefs, animated_emoji_ids: frozenset[int]) -> str:
    match node.node_type:
        case NodeType.TEXT:
            return render_unicode_text_with_emoji_titles(node.text_content or "")
        case NodeType.BOLD:
            return f"<strong>{_render_children(node, refs, animated_emoji_ids)}</strong>"
        case NodeType.ITALIC:
            return f"<em>{_render_children(node, refs, animated_emoji_ids)}</em>"
        case NodeType.UNDERLINE:
            return f"<u>{_render_children(node, refs, animated_emoji_ids)}</u>"
        case NodeType.STRIKETHROUGH:
            return f"<s>{_render_children(node, refs, animated_emoji_ids)}</s>"
        case NodeType.CODE_INLINE:
            return f"<code>{_render_children(node, refs, animated_emoji_ids)}</code>"
        case NodeType.CODE_BLOCK:
            lang_class = (
                f' class="language-{html.escape(node.code_lang)}"' if node.code_lang else ""
            )
            return (
                f"<pre><code{lang_class}>"
                f"{_render_children(node, refs, animated_emoji_ids)}</code></pre>"
            )
        case NodeType.QUOTE_BLOCK:
            return f"<blockquote>{_render_children(node, refs, animated_emoji_ids)}</blockquote>"
        case NodeType.SPOILER:
            return (
                '<details class="spoiler"><summary>Spoiler</summary>'
                f"{_render_children(node, refs, animated_emoji_ids)}</details>"
            )
        case NodeType.USER:
            name = refs.users.get(node.discord_id, "unknown user")
            return f'<span class="mention mention-user">@{html.escape(name)}</span>'
        case NodeType.ROLE:
            # No `roles` table exists to resolve against (ROADMAP.md §3) --
            # always rendered as an inert placeholder.
            return '<span class="mention mention-role">@unknown role</span>'
        case NodeType.CHANNEL:
            name = refs.channels.get(node.discord_id, "unknown-channel")
            return (
                f'<span class="mention mention-channel" data-channel-id="{node.discord_id}">'
                f"#{html.escape(name)}</span>"
            )
        case NodeType.EMOJI_CUSTOM:
            return render_custom_emoji_html(
                emoji_id=node.discord_id,
                name=node.emoji_name,
                animated=node.discord_id in animated_emoji_ids,
            )
        case NodeType.EMOJI_UNICODE_ENCODED:
            # Discord's own client resolves :name: shortcodes to real unicode
            # before sending; this only appears via bots/webhooks bypassing
            # that. Discord's client still renders a recognized shortcode as
            # the real emoji (confirmed against a live example), so resolve
            # it the same way rather than showing literal shortcode text --
            # falling back to the literal text only for a genuinely
            # unrecognized name.
            resolved = resolve_shortcode_to_unicode(node.emoji_name or "")
            if resolved is None:
                return html.escape(f":{node.emoji_name}:")
            return render_unicode_text_with_emoji_titles(resolved)
        case NodeType.URL_WITH_PREVIEW | NodeType.URL_WITHOUT_PREVIEW:
            # No distinct preview-card feature exists in this project (that's
            # embeds.py's job, driven by Discord's own structured embed data,
            # not ad-hoc link scraping) -- both node types get an identical
            # plain link. url can only ever contain http(s):// plus a fixed
            # safe character class (see lexer.py's URL_REGEX), so it can't
            # itself carry a javascript: scheme or break out of the
            # attribute.
            safe_url = html.escape(node.url or "", quote=True)
            return f'<a href="{safe_url}">{html.escape(node.url or "")}</a>'
        case _:
            raise AssertionError(f"unhandled markdown node type: {node.node_type}")
