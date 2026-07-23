from datetime import UTC, datetime

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for

from threadbare.db import queries
from threadbare.pagination import page_number_for_offset
from threadbare.rendering.render_service import render_message_for_display
from threadbare.web import authz
from threadbare.web.breadcrumbs import topic_breadcrumbs

bp = Blueprint("topic", __name__)


@bp.route("/topic/<int:thread_id>")
async def topic_index(thread_id: int):
    return redirect(url_for("topic.topic_page", thread_id=thread_id, page=1))


@bp.route("/topic/<int:thread_id>/page/<int:page>")
async def topic_page(thread_id: int, page: int):
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
            conn, thread_id=thread_id, page=page, page_size=g.posts_per_page
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
        return url_for("topic.topic_page", thread_id=thread_id, page=n)

    return render_template(
        "topic.html",
        thread=thread,
        breadcrumbs=breadcrumbs,
        posts=posts,
        page=page,
        total_pages=total_pages,
        page_url=page_url,
        jump_action=url_for("topic.topic_jump_to_page", thread_id=thread_id),
    )


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
    return redirect(url_for("topic.topic_page", thread_id=thread_id, page=page))
