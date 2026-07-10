from flask import Blueprint, current_app, render_template

from threadbare.db import queries
from threadbare.web.board_tree import group_channels_by_category

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

    return render_template(
        "board_index.html", groups=groups, aggregates=aggregates, authors=authors
    )
