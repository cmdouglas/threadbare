from datetime import UTC, datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from threadbare.channel_types import NON_CONTENT_TYPES
from threadbare.db import queries
from threadbare.pagination import DEFAULT_PAGE_SIZE, page_number_for_offset
from threadbare.pseudotopics import week_bounds
from threadbare.read_status import is_unread
from threadbare.rendering.render_service import render_message_for_display
from threadbare.web import authz
from threadbare.web.board_tree import board_view_mode
from threadbare.web.breadcrumbs import board_breadcrumbs

bp = Blueprint("board", __name__)


async def _get_board_or_404(conn, channel_id: int) -> dict:
    channel = await queries.get_channel(conn, channel_id)
    if channel is None or channel["type"] in NON_CONTENT_TYPES:
        abort(404)
    if not authz.channel_passes_visibility_gate(channel, g.visible_channel_ids):
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
        breadcrumbs = await board_breadcrumbs(conn, channel, script_root=request.script_root)

        total_topics = await queries.count_topics_for_board(conn, channel_id)
        threads = await queries.get_threads_for_board(
            conn, channel_id, page=page, page_size=DEFAULT_PAGE_SIZE
        )
        aggregates = await queries.get_thread_post_aggregates(conn, [t["id"] for t in threads])
        author_ids = {a["last_author_id"] for a in aggregates.values() if a["last_author_id"]}
        authors = await queries.resolve_users(conn, author_ids)
        markers = await queries.get_read_markers(
            conn,
            user_id=session["user_id"],
            channel_ids=[],
            thread_ids=[t["id"] for t in threads],
        )
        unread_threads = {
            thread["id"]: is_unread(aggregates.get(thread["id"]), markers.get(thread["id"]))
            for thread in threads
        }

    total_pages = page_number_for_offset(total_topics - 1) if total_topics > 0 else 1

    def page_url(n: int) -> str:
        return url_for("board.board_topics", channel_id=channel_id, page=n)

    return render_template(
        "board_topic_list.html",
        channel=channel,
        mode=mode,
        breadcrumbs=breadcrumbs,
        threads=threads,
        aggregates=aggregates,
        authors=authors,
        unread_threads=unread_threads,
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
        breadcrumbs = await board_breadcrumbs(conn, channel, script_root=request.script_root)
        total = await queries.count_messages_before(conn, channel_id=channel_id)
        rows = await queries.get_messages_page(
            conn, channel_id=channel_id, page=page, page_size=g.posts_per_page, total=total
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
        if rows:
            last = rows[-1]
            await queries.mark_read(
                conn,
                user_id=session["user_id"],
                channel_id=channel_id,
                message_id=last["id"],
                posted_at=last["posted_at"],
            )

    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1

    def page_url(n: int) -> str:
        return url_for("board.board_continuous_page", channel_id=channel_id, page=n)

    return render_template(
        "board_continuous.html",
        channel=channel,
        heading=channel["name"],
        breadcrumbs=breadcrumbs,
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


@bp.route("/board/<int:channel_id>/continuous/jump_to_unread")
async def board_continuous_jump_to_unread(channel_id: int):
    """First-unread-post jump (DESIGN.md §7 Phase 3), shared by both the
    continuous and weekly views since the read marker is per-channel, not
    per-view. No marker at all means nothing's been read -- straight to
    page 1, same as a brand-new visitor. Otherwise reuses
    count_messages_before's existing (posted_at, id)-tuple comparison
    (already built for permalinks/jump-to-date) rather than a new query:
    the count of messages strictly before the marker is how many are
    already read, so the next one -- offset = that count + 1 -- is the
    first unread post.
    """
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        await _get_board_or_404(conn, channel_id)
        total = await queries.count_messages_before(conn, channel_id=channel_id)
        marker = await queries.get_read_marker(
            conn, user_id=session["user_id"], channel_id=channel_id
        )
        if marker is None:
            page = 1
        else:
            preceding = await queries.count_messages_before(
                conn,
                channel_id=channel_id,
                before=(marker["last_read_posted_at"], marker["last_read_message_id"]),
            )
            page = page_number_for_offset(preceding + 1, page_size=g.posts_per_page)
    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1
    page = min(page, total_pages)
    return redirect(url_for("board.board_continuous_page", channel_id=channel_id, page=page))


@bp.route("/board/<int:channel_id>/weeks")
async def board_weeks_index(channel_id: int):
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        breadcrumbs = await board_breadcrumbs(conn, channel, script_root=request.script_root)
        weeks = await queries.get_weeks_for_board(conn, channel_id)

    return render_template(
        "board_weeks.html", channel=channel, breadcrumbs=breadcrumbs, weeks=weeks
    )


@bp.route("/board/<int:channel_id>/week/<week_id>/page/<int:page>")
async def board_week_page(channel_id: int, week_id: str, page: int):
    since, until = week_bounds(week_id)
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        breadcrumbs = await board_breadcrumbs(conn, channel, script_root=request.script_root)
        total = await queries.count_messages_before(
            conn, channel_id=channel_id, since=since, until=until
        )
        rows = await queries.get_messages_page(
            conn,
            channel_id=channel_id,
            page=page,
            page_size=g.posts_per_page,
            total=total,
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
        if rows:
            last = rows[-1]
            await queries.mark_read(
                conn,
                user_id=session["user_id"],
                channel_id=channel_id,
                message_id=last["id"],
                posted_at=last["posted_at"],
            )

    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1

    def page_url(n: int) -> str:
        return url_for("board.board_week_page", channel_id=channel_id, week_id=week_id, page=n)

    return render_template(
        "board_continuous.html",
        channel=channel,
        heading=f"{channel['name']} — week {week_id}",
        breadcrumbs=breadcrumbs,
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
