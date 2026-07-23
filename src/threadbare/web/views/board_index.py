from flask import Blueprint, current_app, g, render_template, url_for

from threadbare.db import queries
from threadbare.pagination import DEFAULT_PAGE_SIZE, page_number_for_offset
from threadbare.web.board_tree import board_view_mode, group_channels_by_category

bp = Blueprint("board_index", __name__)


@bp.route("/")
async def board_index():
    settings = current_app.config["SETTINGS"]
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        rows = await queries.get_boards_and_categories(conn, settings.discord_guild_id)
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

    return render_template(
        "board_index.html",
        groups=groups,
        aggregates=aggregates,
        authors=authors,
        board_total_pages=board_total_pages,
        board_jump_action=board_jump_action,
    )
