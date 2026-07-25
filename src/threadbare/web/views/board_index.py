from flask import Blueprint, current_app, g, render_template, session, url_for

from threadbare.db import queries
from threadbare.pagination import DEFAULT_PAGE_SIZE, page_number_for_offset
from threadbare.read_status import is_unread
from threadbare.web.board_tree import board_view_mode, group_channels_by_category

bp = Blueprint("board_index", __name__)


@bp.route("/")
async def board_index():
    settings = current_app.config["SETTINGS"]
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        rows = await queries.get_boards_and_categories(
            conn, settings.discord_guild_id, visible_channel_ids=g.visible_channel_ids
        )
        groups = group_channels_by_category(rows)

        board_ids = [row["id"] for group in groups for row in group["boards"]]
        aggregates = await queries.get_board_post_aggregates(conn, board_ids)
        author_ids = {a["last_author_id"] for a in aggregates.values() if a["last_author_id"]}
        authors = await queries.resolve_users(conn, author_ids)

        # One query per board rather than a batched ANY(%s) variant -- typical
        # servers have a handful to a few dozen channels, not thousands, so
        # this is a fine trade against building batch-count query variants
        # nothing else would ever need (ROADMAP.md already reasoned the whole
        # feature is only worth it at this scale).
        board_total_pages: dict[int, int] = {}
        board_jump_action: dict[int, str] = {}
        for group in groups:
            for board in group["boards"]:
                if board_view_mode(board) == "freeform":
                    total = await queries.count_messages_before(conn, channel_id=board["id"])
                    page_size = g.posts_per_page
                    board_jump_action[board["id"]] = url_for(
                        "board.board_continuous_jump_to_page", channel_id=board["id"]
                    )
                else:
                    total = await queries.count_topics_for_board(conn, board["id"])
                    page_size = DEFAULT_PAGE_SIZE
                    board_jump_action[board["id"]] = url_for(
                        "board.board_topics", channel_id=board["id"]
                    )
                board_total_pages[board["id"]] = (
                    page_number_for_offset(total - 1, page_size=page_size) if total > 0 else 1
                )

        # Unread badges only cover freeform boards here: a freeform board's
        # read marker is the board's own channel_id (set by
        # board.board_continuous_page/board_week_page), so comparing it
        # against this same board's aggregate is a direct, accurate check.
        # A topics_only board never gets a channel-level marker at all --
        # reading happens per-topic (topic.topic_page) -- so an accurate
        # badge here would need every one of its threads' read state
        # aggregated up, which is real added scope left for later rather
        # than showing a badge that would just always read "unread".
        freeform_board_ids = [
            board["id"]
            for group in groups
            for board in group["boards"]
            if board_view_mode(board) == "freeform"
        ]
        markers = await queries.get_read_markers(
            conn, user_id=session["user_id"], channel_ids=freeform_board_ids, thread_ids=[]
        )
        unread_boards = {
            board_id: is_unread(aggregates.get(board_id), markers.get(board_id))
            for board_id in freeform_board_ids
        }
        # A marker's presence (regardless of is_unread) means the board
        # isn't entirely unread -- some prefix of it has been read, so
        # "jump to first unread" is meaningfully different from just
        # opening the board at page 1. No marker at all means nothing's
        # ever been read, where the two would land on the same place, so
        # the link stays hidden rather than being a no-op next to the
        # board name link.
        board_jump_to_unread_action = {
            board_id: url_for("board.board_continuous_jump_to_unread", channel_id=board_id)
            for board_id in freeform_board_ids
            if board_id in markers
        }

    return render_template(
        "board_index.html",
        groups=groups,
        aggregates=aggregates,
        authors=authors,
        board_total_pages=board_total_pages,
        board_jump_action=board_jump_action,
        unread_boards=unread_boards,
        board_jump_to_unread_action=board_jump_to_unread_action,
    )
