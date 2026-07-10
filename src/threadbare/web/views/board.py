from datetime import UTC, datetime

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from threadbare.channel_types import CATEGORY
from threadbare.db import queries
from threadbare.pagination import DEFAULT_PAGE_SIZE, page_number_for_offset
from threadbare.pseudotopics import week_bounds
from threadbare.rendering.render_service import render_message_for_display
from threadbare.web.board_tree import board_view_mode

bp = Blueprint("board", __name__)


async def _get_board_or_404(conn, channel_id: int) -> dict:
    channel = await queries.get_channel(conn, channel_id)
    if channel is None or channel["type"] == CATEGORY:
        abort(404)
    return channel


@bp.route("/board/<int:channel_id>")
async def board_landing(channel_id: int):
    page = max(request.args.get("page", default=1, type=int) or 1, 1)
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        mode = board_view_mode(channel)

        total_topics = await queries.count_topics_for_board(conn, channel_id)
        threads = await queries.get_threads_for_board(
            conn, channel_id, page=page, page_size=DEFAULT_PAGE_SIZE
        )
        aggregates = await queries.get_thread_post_aggregates(conn, [t["id"] for t in threads])
        author_ids = {a["last_author_id"] for a in aggregates.values() if a["last_author_id"]}
        authors = await queries.resolve_users(conn, author_ids)

    total_pages = page_number_for_offset(total_topics - 1) if total_topics > 0 else 1

    def page_url(n: int) -> str:
        return url_for("board.board_landing", channel_id=channel_id, page=n)

    return render_template(
        "board_topic_list.html",
        channel=channel,
        mode=mode,
        threads=threads,
        aggregates=aggregates,
        authors=authors,
        page=page,
        total_pages=total_pages,
        page_url=page_url,
    )


@bp.route("/board/<int:channel_id>/continuous")
async def board_continuous_index(channel_id: int):
    return redirect(url_for("board.board_continuous_page", channel_id=channel_id, page=1))


@bp.route("/board/<int:channel_id>/continuous/page/<int:page>")
async def board_continuous_page(channel_id: int, page: int):
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        total = await queries.count_messages_before(conn, channel_id=channel_id)
        rows = await queries.get_messages_page(
            conn, channel_id=channel_id, page=page, page_size=DEFAULT_PAGE_SIZE
        )
        posts = [(row, await render_message_for_display(conn, row)) for row in rows]

    total_pages = page_number_for_offset(total - 1) if total > 0 else 1

    def page_url(n: int) -> str:
        return url_for("board.board_continuous_page", channel_id=channel_id, page=n)

    return render_template(
        "board_continuous.html",
        channel=channel,
        heading=channel["name"],
        posts=posts,
        page=page,
        total_pages=total_pages,
        page_url=page_url,
    )


@bp.route("/board/<int:channel_id>/continuous/jump")
async def board_continuous_jump(channel_id: int):
    try:
        target_date = datetime.strptime(request.args.get("date", ""), "%Y-%m-%d").replace(
            tzinfo=UTC
        )
    except ValueError:
        abort(400)

    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        preceding = await queries.count_messages_before(
            conn, channel_id=channel_id, before=target_date
        )
    page = page_number_for_offset(preceding)
    return redirect(url_for("board.board_continuous_page", channel_id=channel_id, page=page))


@bp.route("/board/<int:channel_id>/weeks")
async def board_weeks_index(channel_id: int):
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        weeks = await queries.get_weeks_for_board(conn, channel_id)

    return render_template("board_weeks.html", channel=channel, weeks=weeks)


@bp.route("/board/<int:channel_id>/week/<week_id>/page/<int:page>")
async def board_week_page(channel_id: int, week_id: str, page: int):
    since, until = week_bounds(week_id)
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        total = await queries.count_messages_before(
            conn, channel_id=channel_id, since=since, until=until
        )
        rows = await queries.get_messages_page(
            conn,
            channel_id=channel_id,
            page=page,
            page_size=DEFAULT_PAGE_SIZE,
            since=since,
            until=until,
        )
        posts = [(row, await render_message_for_display(conn, row)) for row in rows]

    total_pages = page_number_for_offset(total - 1) if total > 0 else 1

    def page_url(n: int) -> str:
        return url_for("board.board_week_page", channel_id=channel_id, week_id=week_id, page=n)

    return render_template(
        "board_continuous.html",
        channel=channel,
        heading=f"{channel['name']} — week {week_id}",
        posts=posts,
        page=page,
        total_pages=total_pages,
        page_url=page_url,
    )
