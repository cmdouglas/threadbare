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


@bp.route("/search")
async def search():
    query = request.args.get("q", "").strip()
    author = request.args.get("author") or None
    channel_id = request.args.get("channel", type=int)
    after = _parse_date(request.args.get("after"))
    before = _parse_date(request.args.get("before"))
    page = max(request.args.get("page", default=1, type=int) or 1, 1)

    results: list[dict] = []
    total = 0
    if query:
        pool = current_app.config["POOL"]
        async with pool.connection() as conn:
            results = await queries.search_messages(
                conn,
                query=query,
                author=author,
                channel_id=channel_id,
                after=after,
                before=before,
                page=page,
                page_size=g.posts_per_page,
            )
            total = await queries.count_search_results(
                conn, query=query, author=author, channel_id=channel_id, after=after, before=before
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
    )
