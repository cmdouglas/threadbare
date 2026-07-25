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
        thread_unread_counts = {
            thread["id"]: await queries.count_unread(
                conn,
                marker=markers.get(thread["id"]),
                total=aggregates.get(thread["id"], {}).get("post_count", 0),
                thread_id=thread["id"],
            )
            for thread in threads
        }
        unread_threads = {thread_id: count > 0 for thread_id, count in thread_unread_counts.items()}
        # Same "partially read, not never-read or fully-read" reasoning as
        # board_index -- see that view's board_jump_to_unread_action.
        thread_jump_to_unread_action = {
            thread["id"]: url_for("topic.topic_jump_to_unread", thread_id=thread["id"])
            for thread in threads
            if thread["id"] in markers and unread_threads.get(thread["id"])
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
        thread_unread_counts=thread_unread_counts,
        thread_jump_to_unread_action=thread_jump_to_unread_action,
        visited_threads=set(markers),
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
    reaction = request.args.get("reaction") or None
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        breadcrumbs = await board_breadcrumbs(conn, channel, script_root=request.script_root)
        total = await queries.count_messages_before(conn, channel_id=channel_id, reaction=reaction)
        rows = await queries.get_messages_page(
            conn,
            channel_id=channel_id,
            page=page,
            page_size=g.posts_per_page,
            total=total,
            reaction=reaction,
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
        # A reaction-filtered page's rows[-1] is not the channel's true
        # last-shown message -- marking read to it would silently
        # fast-forward the marker past unfiltered messages the requester
        # never actually saw, so mark_read is skipped entirely while a
        # filter is active.
        if rows and reaction is None:
            last = rows[-1]
            await queries.mark_read(
                conn,
                user_id=session["user_id"],
                channel_id=channel_id,
                message_id=last["id"],
                posted_at=last["posted_at"],
            )
        unread_total = (
            total
            if reaction is None
            else await queries.count_messages_before(conn, channel_id=channel_id)
        )
        show_jump_to_unread = await queries.has_unread(
            conn, user_id=session["user_id"], channel_id=channel_id, total=unread_total
        )
        available_reactions = await queries.get_reactions_present_in_container(
            conn, channel_id=channel_id
        )

    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1

    def page_url(n: int) -> str:
        return url_for(
            "board.board_continuous_page", channel_id=channel_id, page=n, reaction=reaction
        )

    def reaction_filter_url(r: str | None) -> str:
        return url_for("board.board_continuous_page", channel_id=channel_id, page=1, reaction=r)

    return render_template(
        "board_continuous.html",
        channel=channel,
        heading=channel["name"],
        breadcrumbs=breadcrumbs,
        posts=posts,
        page=page,
        total_pages=total_pages,
        page_url=page_url,
        show_jump_to_unread=show_jump_to_unread,
        jump_action=url_for("board.board_continuous_jump_to_page", channel_id=channel_id),
        reaction=reaction,
        available_reactions=available_reactions,
        reaction_filter_url=reaction_filter_url,
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
    reaction = request.args.get("reaction") or None
    return redirect(
        url_for("board.board_continuous_page", channel_id=channel_id, page=page, reaction=reaction)
    )


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
    reaction = request.args.get("reaction") or None
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channel = await _get_board_or_404(conn, channel_id)
        breadcrumbs = await board_breadcrumbs(conn, channel, script_root=request.script_root)
        total = await queries.count_messages_before(
            conn, channel_id=channel_id, since=since, until=until, reaction=reaction
        )
        rows = await queries.get_messages_page(
            conn,
            channel_id=channel_id,
            page=page,
            page_size=g.posts_per_page,
            total=total,
            since=since,
            until=until,
            reaction=reaction,
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
        # Same reasoning as board_continuous_page: a filtered page's
        # rows[-1] doesn't represent everything up to that point, so
        # mark_read is skipped while a reaction filter is active.
        if rows and reaction is None:
            last = rows[-1]
            await queries.mark_read(
                conn,
                user_id=session["user_id"],
                channel_id=channel_id,
                message_id=last["id"],
                posted_at=last["posted_at"],
            )
        # has_unread needs the channel's real, unscoped total -- the `total`
        # above is this week's count only, and reading progress is tracked
        # per-channel, not per-week, so "last page of this week" doesn't
        # mean "channel fully read" the way it does on the continuous view.
        # Deliberately unfiltered by reaction too, for the same reason.
        channel_total = await queries.count_messages_before(conn, channel_id=channel_id)
        show_jump_to_unread = await queries.has_unread(
            conn, user_id=session["user_id"], channel_id=channel_id, total=channel_total
        )
        available_reactions = await queries.get_reactions_present_in_container(
            conn, channel_id=channel_id, since=since, until=until
        )

    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1

    def page_url(n: int) -> str:
        return url_for(
            "board.board_week_page",
            channel_id=channel_id,
            week_id=week_id,
            page=n,
            reaction=reaction,
        )

    def reaction_filter_url(r: str | None) -> str:
        return url_for(
            "board.board_week_page", channel_id=channel_id, week_id=week_id, page=1, reaction=r
        )

    return render_template(
        "board_continuous.html",
        channel=channel,
        heading=f"{channel['name']} — week {week_id}",
        breadcrumbs=breadcrumbs,
        posts=posts,
        page=page,
        total_pages=total_pages,
        page_url=page_url,
        show_jump_to_unread=show_jump_to_unread,
        jump_action=url_for(
            "board.board_week_jump_to_page", channel_id=channel_id, week_id=week_id
        ),
        reaction=reaction,
        available_reactions=available_reactions,
        reaction_filter_url=reaction_filter_url,
    )


@bp.route("/board/<int:channel_id>/week/<week_id>/jump_to_page")
async def board_week_jump_to_page(channel_id: int, week_id: str):
    page = max(request.args.get("page", type=int) or 1, 1)
    reaction = request.args.get("reaction") or None
    return redirect(
        url_for(
            "board.board_week_page",
            channel_id=channel_id,
            week_id=week_id,
            page=page,
            reaction=reaction,
        )
    )
