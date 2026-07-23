from flask import Blueprint, abort, current_app, g, render_template, request

from threadbare.db import queries
from threadbare.pagination import page_number_for_offset
from threadbare.rendering.render_service import render_message_for_display

bp = Blueprint("user", __name__)


@bp.route("/user/<int:user_id>")
async def user_page(user_id: int):
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        user = await queries.get_user(conn, user_id)
        if user is None:
            abort(404)
        roles = await queries.get_roles_by_ids(conn, user["role_ids"])
        post_count = await queries.get_post_count_for_user(
            conn, user_id, visible_channel_ids=g.visible_channel_ids
        )
        recent_rows = await queries.get_recent_posts_for_user(
            conn, user_id, visible_channel_ids=g.visible_channel_ids, limit=10
        )

        posts = []
        for row in recent_rows:
            rendered = await render_message_for_display(
                conn, row, script_root=request.script_root, page_size=g.posts_per_page
            )
            # Each recent post can live in a different topic/board, so
            # (unlike topic.html/board_continuous.html) there's no single
            # shared page number -- compute each post's own permalink page.
            preceding = await queries.count_messages_before(
                conn,
                thread_id=row["thread_id"],
                channel_id=row["channel_id"],
                before=(row["posted_at"], row["id"]),
            )
            posts.append(
                (row, rendered, page_number_for_offset(preceding, page_size=g.posts_per_page))
            )

    return render_template(
        "user.html", profile=user, roles=roles, post_count=post_count, posts=posts
    )
