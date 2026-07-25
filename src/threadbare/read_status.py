"""Pure "is this container unread for this reader" comparison (DESIGN.md
§7 Phase 3), shared by the board index (channels) and a board's topic list
(threads) -- both compare a get_board_post_aggregates/
get_thread_post_aggregates row against a db.queries.get_read_markers row,
so the comparison itself doesn't need to know which kind of container it's
looking at.
"""


def is_unread(aggregate: dict | None, marker: dict | None) -> bool:
    if aggregate is None or aggregate["last_message_id"] is None:
        return False
    if marker is None:
        return True
    return (aggregate["last_posted_at"], aggregate["last_message_id"]) > (
        marker["last_read_posted_at"],
        marker["last_read_message_id"],
    )
