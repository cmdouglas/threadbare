from datetime import UTC, datetime

from flask import Blueprint, current_app, g, render_template, request, url_for

from threadbare.db import queries
from threadbare.pagination import page_number_for_offset

bp = Blueprint("search", __name__)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _make_page_url():
    args = request.args.to_dict()

    def page_url(page: int) -> str:
        return url_for("search.search", **{**args, "page": page})

    return page_url


def _clear_author_url():
    args = {k: v for k, v in request.args.to_dict().items() if k not in ("author_id", "page")}
    return url_for("search.search", **args)


@bp.route("/search")
async def search():
    query = request.args.get("q", "").strip()
    author = request.args.get("author") or None
    author_id = request.args.get("author_id", type=int)
    channel_id = request.args.get("channel", type=int)
    after = _parse_date(request.args.get("after"))
    before = _parse_date(request.args.get("before"))
    page = max(request.args.get("page", default=1, type=int) or 1, 1)

    results: list[dict] = []
    total = 0
    author_display_name = None
    if author_id is not None:
        pool = current_app.config["POOL"]
        async with pool.connection() as conn:
            author_row = await queries.get_user(conn, author_id)
        if author_row is not None:
            author_display_name = author_row["display_name"]
    if query:
        pool = current_app.config["POOL"]
        async with pool.connection() as conn:
            results = await queries.search_messages(
                conn,
                query=query,
                author=author,
                author_id=author_id,
                channel_id=channel_id,
                after=after,
                before=before,
                page=page,
                page_size=g.posts_per_page,
                visible_channel_ids=g.visible_channel_ids,
            )
            total = await queries.count_search_results(
                conn,
                query=query,
                author=author,
                author_id=author_id,
                channel_id=channel_id,
                after=after,
                before=before,
                visible_channel_ids=g.visible_channel_ids,
            )
        for row in results:
            row["page"] = page_number_for_offset(row["preceding_count"], page_size=g.posts_per_page)

    total_pages = page_number_for_offset(total - 1, page_size=g.posts_per_page) if total > 0 else 1

    return render_template(
        "search_results.html",
        query=query,
        results=results,
        total=total,
        page=page,
        total_pages=total_pages,
        page_url=_make_page_url(),
        jump_action=url_for("search.search"),
        author_id=author_id,
        author_display_name=author_display_name,
        clear_author_url=_clear_author_url() if author_id is not None else None,
    )
