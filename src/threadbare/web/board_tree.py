"""Pure grouping/classification helpers over channel rows for the board
index and board landing pages. No I/O -- callers fetch rows first.
"""

from threadbare.channel_types import CATEGORY, FORUM_LIKE_TYPES, NON_CONTENT_TYPES


def board_view_mode(channel_row: dict) -> str:
    """Returns "topics_only" for forum/media channels (no direct messages by
    construction -- every post is a thread), else "freeform": a text/news
    channel can hold both direct messages and native Discord threads, so it
    needs both the topic list and the continuous/weekly controls.
    """
    if channel_row["type"] in FORUM_LIKE_TYPES:
        return "topics_only"
    return "freeform"


def group_channels_by_category(rows: list[dict]) -> list[dict]:
    """Groups channel rows for the board index: [{"category": row | None,
    "boards": [rows]}, ...], ordered by category position (uncategorized
    boards first, as a category=None group), then board position within
    each group. A group -- uncategorized or a named category -- is omitted
    entirely if it has no boards: a category whose only children are
    voice/stage-voice channels (which get no board row at all, see
    NON_CONTENT_TYPES) would otherwise render as an empty section header
    with nothing under it.

    A board whose parent_id isn't among the given rows' categories (e.g. its
    category was filtered out upstream for not being public) is folded into
    the uncategorized group rather than silently dropped.
    """
    categories = {row["id"]: row for row in rows if row["type"] == CATEGORY}
    boards = sorted(
        (row for row in rows if row["type"] not in NON_CONTENT_TYPES),
        key=lambda r: r["position"],
    )

    groups: dict[int | None, list[dict]] = {None: [], **{cid: [] for cid in categories}}
    for board in boards:
        parent_id = board["parent_id"] if board["parent_id"] in categories else None
        groups[parent_id].append(board)

    ordered_category_ids = sorted(categories, key=lambda cid: categories[cid]["position"])

    result = []
    if groups[None]:
        result.append({"category": None, "boards": groups[None]})
    for cid in ordered_category_ids:
        if groups[cid]:
            result.append({"category": categories[cid], "boards": groups[cid]})
    return result
