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

from threadbare.db import queries
from threadbare.pagination import page_number_for_offset
from threadbare.rendering.render_service import render_message_for_display
from threadbare.reply_tree import build_reply_tree
from threadbare.web import authz
from threadbare.web.breadcrumbs import topic_breadcrumbs

bp = Blueprint("topic", __name__)


@bp.route("/topic/<int:thread_id>")
async def topic_index(thread_id: int):
    return redirect(url_for("topic.topic_page", thread_id=thread_id, page=1))


@bp.route("/topic/<int:thread_id>/page/<int:page>")
async def topic_page(thread_id: int, page: int):
    reaction = request.args.get("reaction") or None
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        thread = await queries.get_thread(conn, thread_id)
        if thread is None:
            abort(404)
        channel = await queries.get_channel(conn, thread["parent_channel_id"])
        if channel is None or not authz.channel_passes_visibility_gate(
            channel, g.visible_channel_ids
        ):
            abort(404)
        breadcrumbs = await topic_breadcrumbs(conn, thread, script_root=request.script_root)
        total = await queries.count_messages_before(conn, thread_id=thread_id, reaction=reaction)
        rows = await queries.get_messages_page(
            conn,
            thread_id=thread_id,
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
        # Same reasoning as board_continuous_page: a reaction-filtered
        # page's rows[-1] isn't the thread's true last-shown message, so
        # mark_read is skipped while a filter is active.
        if rows and reaction is None:
            last = rows[-1]
            await queries.mark_read(
                conn,
                user_id=session["user_id"],
                thread_id=thread_id,
                message_id=last["id"],
                posted_at=last["posted_at"],
            )
        unread_total = (
            total
            if reaction is None
            else await queries.count_messages_before(conn, thread_id=thread_id)
        )
        show_jump_to_unread = await queries.has_unread(
            conn, user_id=session["user_id"], thread_id=thread_id, total=unread_total
        )
        available_reactions = await queries.get_reactions_present_in_container(
            conn, thread_id=thread_id
        )

    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1

    def page_url(n: int) -> str:
        return url_for("topic.topic_page", thread_id=thread_id, page=n, reaction=reaction)

    def reaction_filter_url(r: str | None) -> str:
        return url_for("topic.topic_page", thread_id=thread_id, page=1, reaction=r)

    return render_template(
        "topic.html",
        thread=thread,
        breadcrumbs=breadcrumbs,
        posts=posts,
        page=page,
        total_pages=total_pages,
        page_url=page_url,
        show_jump_to_unread=show_jump_to_unread,
        jump_action=url_for("topic.topic_jump_to_page", thread_id=thread_id),
        reaction=reaction,
        available_reactions=available_reactions,
        reaction_filter_url=reaction_filter_url,
    )


@bp.route("/topic/<int:thread_id>/tree")
async def topic_tree_view(thread_id: int):
    """Reply-chain threading view (DESIGN.md §7 Phase 3): the same messages
    as the flat paginated view, nested by messages.reply_to_id instead of
    split across pages -- an alternative lens on one topic, not a separate
    dataset. Deliberately unpaginated: a tree only makes sense shown whole,
    and Discord threads are naturally bounded in a way freeform-channel
    continuous browsing isn't, so loading every message in one request is
    accepted here where it wouldn't be for a board.
    """
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        thread = await queries.get_thread(conn, thread_id)
        if thread is None:
            abort(404)
        channel = await queries.get_channel(conn, thread["parent_channel_id"])
        if channel is None or not authz.channel_passes_visibility_gate(
            channel, g.visible_channel_ids
        ):
            abort(404)
        breadcrumbs = await topic_breadcrumbs(conn, thread, script_root=request.script_root)
        total = await queries.count_messages_before(conn, thread_id=thread_id)
        rows = await queries.get_messages_page(
            conn, thread_id=thread_id, page=1, page_size=total, total=total
        )
        # A row's index in `rows` is exactly its own preceding-count, since
        # both share the same chronological order -- reuses
        # page_number_for_offset instead of a per-row count query, so a
        # reply's permalink still lands on its real page in the flat view.
        pages_by_id = {
            row["id"]: page_number_for_offset(i, page_size=g.posts_per_page)
            for i, row in enumerate(rows)
        }

        async def render_node(node):
            row = node["row"]
            rendered = await render_message_for_display(
                conn, row, script_root=request.script_root, page_size=g.posts_per_page
            )
            children = [await render_node(child) for child in node["children"]]
            return {
                "row": row,
                "rendered": rendered,
                "page": pages_by_id[row["id"]],
                "children": children,
            }

        tree = [await render_node(node) for node in build_reply_tree(rows)]

        if rows:
            last = rows[-1]
            await queries.mark_read(
                conn,
                user_id=session["user_id"],
                thread_id=thread_id,
                message_id=last["id"],
                posted_at=last["posted_at"],
            )

    return render_template("topic_tree.html", thread=thread, breadcrumbs=breadcrumbs, tree=tree)


@bp.route("/topic/<int:thread_id>/jump")
async def topic_jump(thread_id: int):
    try:
        target_date = datetime.strptime(request.args.get("date", ""), "%Y-%m-%d").replace(
            tzinfo=UTC
        )
    except ValueError:
        abort(400)

    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        preceding = await queries.count_messages_before(
            conn, thread_id=thread_id, before=target_date
        )
    page = page_number_for_offset(preceding, page_size=g.posts_per_page)
    return redirect(url_for("topic.topic_page", thread_id=thread_id, page=page))


@bp.route("/topic/<int:thread_id>/jump_to_page")
async def topic_jump_to_page(thread_id: int):
    page = max(request.args.get("page", type=int) or 1, 1)
    reaction = request.args.get("reaction") or None
    return redirect(url_for("topic.topic_page", thread_id=thread_id, page=page, reaction=reaction))


@bp.route("/topic/<int:thread_id>/jump_to_unread")
async def topic_jump_to_unread(thread_id: int):
    """First-unread-post jump (DESIGN.md §7 Phase 3) -- thread-scoped twin
    of board.board_continuous_jump_to_unread; see that docstring for the
    (posted_at, id)-tuple reasoning shared by both.
    """
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        thread = await queries.get_thread(conn, thread_id)
        if thread is None:
            abort(404)
        total = await queries.count_messages_before(conn, thread_id=thread_id)
        marker = await queries.get_read_marker(
            conn, user_id=session["user_id"], thread_id=thread_id
        )
        if marker is None:
            page = 1
        else:
            preceding = await queries.count_messages_before(
                conn,
                thread_id=thread_id,
                before=(marker["last_read_posted_at"], marker["last_read_message_id"]),
            )
            page = page_number_for_offset(preceding + 1, page_size=g.posts_per_page)
    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1
    page = min(page, total_pages)
    return redirect(url_for("topic.topic_page", thread_id=thread_id, page=page))
