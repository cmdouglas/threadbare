"""Discord system-message (member joins, boosts, pin notices, ...) -> plain
display text. Pure, no I/O. Deliberately scoped to MessageType values whose
text needs only data already captured here (type/content/author name/
timestamp) -- mirrors a subset of discord.py's own Message.system_content
(discord/message.py:2681-2866). Types needing data this project never
captures (call participants, purchases, polls, role subscriptions, stage
channels, who got added/removed from a thread) fall back to a generic
notice rather than fabricating fidelity we can't have.
"""

from datetime import datetime

# Raw int values from discord.py's MessageType enum (discord/enums.py).
_DEFAULT = 0
_CHANNEL_NAME_CHANGE = 4
_CHANNEL_ICON_CHANGE = 5
_PINS_ADD = 6
_NEW_MEMBER = 7
_PREMIUM_GUILD_SUBSCRIPTION = 8
_PREMIUM_GUILD_TIER_1 = 9
_PREMIUM_GUILD_TIER_2 = 10
_PREMIUM_GUILD_TIER_3 = 11
_CHANNEL_FOLLOW_ADD = 12
_THREAD_CREATED = 18
_REPLY = 19
_EMOJI_ADDED = 63

# default/reply carry real user-authored content -- never system messages.
_CONTENT_TYPES = frozenset({_DEFAULT, _REPLY})

# Discord's own 13-message welcome rotation, picked deterministically by
# created-at timestamp -- matches Message.system_content exactly
# (message.py:2717-2735) so this is bit-for-bit what Discord's client shows.
_NEW_MEMBER_FORMATS = [
    "{0} joined the party.",
    "{0} is here.",
    "Welcome, {0}. We hope you brought pizza.",
    "A wild {0} appeared.",
    "{0} just landed.",
    "{0} just slid into the server.",
    "{0} just showed up!",
    "Welcome {0}. Say hi!",
    "{0} hopped into the server.",
    "Everyone welcome {0}!",
    "Glad you're here, {0}.",
    "Good to see you, {0}.",
    "Yay you made it, {0}!",
]


def is_system_message_type(message_type: int) -> bool:
    return message_type not in _CONTENT_TYPES


def render_system_message_text(
    message_type: int, *, content: str, author_display_name: str, posted_at: datetime
) -> str:
    if message_type == _NEW_MEMBER:
        created_at_ms = int(posted_at.timestamp() * 1000)
        return _NEW_MEMBER_FORMATS[created_at_ms % len(_NEW_MEMBER_FORMATS)].format(
            author_display_name
        )
    if message_type == _PINS_ADD:
        return f"{author_display_name} pinned a message to this channel."
    if message_type == _PREMIUM_GUILD_SUBSCRIPTION:
        if not content:
            return f"{author_display_name} just boosted the server!"
        return f"{author_display_name} just boosted the server **{content}** times!"
    if message_type in (_PREMIUM_GUILD_TIER_1, _PREMIUM_GUILD_TIER_2, _PREMIUM_GUILD_TIER_3):
        level = {
            _PREMIUM_GUILD_TIER_1: 1,
            _PREMIUM_GUILD_TIER_2: 2,
            _PREMIUM_GUILD_TIER_3: 3,
        }[message_type]
        base = (
            f"{author_display_name} just boosted the server!"
            if not content
            else f"{author_display_name} just boosted the server **{content}** times!"
        )
        return f"{base} The server has achieved **Level {level}!**"
    if message_type == _CHANNEL_NAME_CHANGE:
        return f"{author_display_name} changed the channel name: **{content}**"
    if message_type == _CHANNEL_ICON_CHANGE:
        return f"{author_display_name} changed the channel icon."
    if message_type == _THREAD_CREATED:
        return f"{author_display_name} started a thread: **{content}**."
    if message_type == _CHANNEL_FOLLOW_ADD:
        return f"{author_display_name} has added {content} to this channel."
    if message_type == _EMOJI_ADDED:
        return f"{author_display_name} added a new emoji, {content}"
    return "This is a system message."
