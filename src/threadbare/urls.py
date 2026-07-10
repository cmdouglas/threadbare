"""Pure URL builders for the forum web app. rendering/quotes.py and
rendering/attachments.py need these to build hrefs (permalinks, the
attachment proxy) but must never import web/ (which depends on Flask/Jinja
request context) -- so this module lives here, a shared sibling of both,
with hardcoded path templates rather than Flask's url_for.
"""


def board_url(channel_id: int) -> str:
    return f"/board/{channel_id}"


def topic_url(thread_id: int, *, page: int = 1) -> str:
    return f"/topic/{thread_id}/page/{page}"


def continuous_url(channel_id: int, *, page: int = 1) -> str:
    return f"/board/{channel_id}/continuous/page/{page}"


def week_url(channel_id: int, week_id: str, *, page: int = 1) -> str:
    return f"/board/{channel_id}/week/{week_id}/page/{page}"


def user_url(user_id: int) -> str:
    return f"/user/{user_id}"


def attachment_proxy_url(attachment_id: int) -> str:
    return f"/att/{attachment_id}"


def permalink_for_message(message_row: dict, *, page: int) -> str:
    """The canonical, stable URL for one message: a thread/forum-post
    message always resolves into /topic/..., a freeform-channel message
    always resolves into the continuous view -- never the weekly pseudo-topic
    view, which is a secondary browsing mode and never a permalink target.
    Mirrors messages' own container-exclusivity invariant (exactly one of
    thread_id/channel_id is set).
    """
    if message_row["thread_id"] is not None:
        base = topic_url(message_row["thread_id"], page=page)
    else:
        base = continuous_url(message_row["channel_id"], page=page)
    return f"{base}#post-{message_row['id']}"


def discord_deep_link_url(*, guild_id: int, message_row: dict) -> str:
    container_id = (
        message_row["thread_id"]
        if message_row["thread_id"] is not None
        else message_row["channel_id"]
    )
    return f"https://discord.com/channels/{guild_id}/{container_id}/{message_row['id']}"
