"""Page-number resolution shared by the board and topic views.

Both views ask the same two questions -- "which page holds this message" and
"which page holds the first unread one" -- and both get subtly different
answers once consecutive-post merging is on. Keeping the answers here rather
than in each view is what stops the two drifting, the way board.py and
topic.py's pagination already share count_messages_before.
"""

from threadbare.db import queries
from threadbare.pagination import page_number_for_offset


async def page_of_first_unread(
    conn,
    *,
    channel_id: int | None = None,
    thread_id: int | None = None,
    marker: dict | None,
    page_size: int,
    merged: bool,
) -> int:
    """The page holding the first message the reader hasn't seen.

    Unmerged, that's simply one past the marker. Merged, it's the page of the
    *post* containing that message -- which may be a post the reader has
    already partly read, since a marker can sit mid-post. Asking for "the page
    after the marker's page" would skip the rest of that post.

    Callers clamp the result to the real page count; a marker at the very end
    resolves to the last page rather than one past it.
    """
    if marker is None:
        return 1
    position = (marker["last_read_posted_at"], marker["last_read_message_id"])
    container = {"channel_id": channel_id, "thread_id": thread_id}

    if not merged:
        preceding = await queries.count_messages_before(conn, **container, before=position)
        return page_number_for_offset(preceding + 1, page_size=page_size)

    first_unread = await queries.get_first_message_after(conn, **container, after=position)
    if first_unread is None:
        total = await queries.count_messages_before(conn, **container, merged=True)
        return page_number_for_offset(max(total - 1, 0), page_size=page_size)

    preceding = await queries.count_posts_before_message(
        conn,
        **container,
        posted_at=first_unread["posted_at"],
        message_id=first_unread["id"],
        merged=True,
    )
    return page_number_for_offset(preceding, page_size=page_size)
