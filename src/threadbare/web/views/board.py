from datetime import UTC, datetime

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from threadbare.channel_types import NON_CONTENT_TYPES
from threadbare.db import queries
from threadbare.pagination import DEFAULT_PAGE_SIZE, page_number_for_offset
from threadbare.pseudotopics import week_bounds
from threadbare.rendering.render_service import render_message_for_display
from threadbare.web.board_tree import board_view_mode

bp = Blueprint("board", __name__)


async def _get_board_or_404(conn, channel_id: int) -> dict:
    channel = await queries.get_channel(conn, channel_id)
    if channel is None or channel["type"] in NON_CONTENT_TYPES:
        abort(404)
    return channel


@bp.route("/board/<int:channel_id>")
async def board_landing(channel_id: int):
    """Smart-dispatch entrypoint, matching the index-redirect idiom already
    used by board_continuous_index: a freeform (text/news) channel defaults
    to continuous browsing, a topics_only (forum/media) channel has nothing
    else to default to, so it goes straight to the topic list.
    """
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        mode = board_view_mode(channel)

    if mode == "freeform":
        return redirect(url_for("board.board_continuous_page", channel_id=channel_id, page=1))
    return redirect(url_for("board.board_topics", channel_id=channel_id))


@bp.route("/board/<int:channel_id>/topics")
async def board_topics(channel_id: int):
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
        return url_for("board.board_topics", channel_id=channel_id, page=n)

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
        jump_action=url_for("board.board_topics", channel_id=channel_id),
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
            conn, channel_id=channel_id, page=page, page_size=g.posts_per_page
        )
        posts = [
            (
                row,
                await render_message_for_display(
                    conn, row, script_root=request.script_root, page_size=g.posts_per_page
                ),
            )
            for row in rows
        ]

    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1

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
        jump_action=url_for("board.board_continuous_jump_to_page", channel_id=channel_id),
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
    page = page_number_for_offset(preceding, page_size=g.posts_per_page)
    return redirect(url_for("board.board_continuous_page", channel_id=channel_id, page=page))


@bp.route("/board/<int:channel_id>/continuous/jump_to_page")
async def board_continuous_jump_to_page(channel_id: int):
    page = max(request.args.get("page", type=int) or 1, 1)
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
            page_size=g.posts_per_page,
            since=since,
            until=until,
        )
        posts = [
            (
                row,
                await render_message_for_display(
                    conn, row, script_root=request.script_root, page_size=g.posts_per_page
                ),
            )
            for row in rows
        ]

    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1

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
        jump_action=url_for(
            "board.board_week_jump_to_page", channel_id=channel_id, week_id=week_id
        ),
    )


@bp.route("/board/<int:channel_id>/week/<week_id>/jump_to_page")
async def board_week_jump_to_page(channel_id: int, week_id: str):
    page = max(request.args.get("page", type=int) or 1, 1)
    return redirect(
        url_for("board.board_week_page", channel_id=channel_id, week_id=week_id, page=page)
    )
