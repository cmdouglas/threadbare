from datetime import UTC, datetime

from flask import Blueprint, current_app, g, render_template, request, url_for

from threadbare import pagination
from threadbare.channel_types import NON_CONTENT_TYPES
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


def _url_with(**overrides: object) -> str:
    """This route's URL with the current query string, plus overrides. A key set
    to None is dropped. One helper instead of a page-url builder and a
    clear-author builder that each re-derived request.args.to_dict() filtering.
    """
    args = {**request.args.to_dict(), **overrides}
    return url_for("search.search", **{k: v for k, v in args.items() if v is not None})


@bp.route("/search")
async def search():
    query = request.args.get("q", "").strip()
    author = request.args.get("author") or None
    author_id = request.args.get("author_id", type=int)
    channel_id = request.args.get("channel", type=int)
    after = _parse_date(request.args.get("after"))
    before = _parse_date(request.args.get("before"))
    reaction = request.args.get("reaction") or None
    page = max(request.args.get("page", default=1, type=int) or 1, 1)

    results: list[dict] = []
    total = 0
    author_display_name = None
    settings = current_app.config["SETTINGS"]
    pool = current_app.config["POOL"]
    # One connection for the whole request: web/db.py opens a fresh connection
    # per .connection() call (no pooling survives Flask's async_to_sync
    # bridge), so the author lookup and the search itself used to cost two
    # separate handshakes on top of app.py's before_request hooks.
    async with pool.connection() as conn:
        if author_id is not None:
            author_row = await queries.get_user(conn, author_id)
            if author_row is not None:
                author_display_name = author_row["display_name"]

        # Boards the requester can actually see, for the channel <select> --
        # this used to be a raw numeric "Channel ID" input, in an app whose
        # wizard exists partly so a mod never has to hand-type a snowflake.
        channel_choices = [
            {"id": row["id"], "name": row["name"]}
            for row in await queries.get_boards_and_categories(
                conn, settings.discord_guild_id, visible_channel_ids=g.visible_channel_ids
            )
            if row["type"] not in NON_CONTENT_TYPES
        ]

        if query:
            results = await queries.search_messages(
                conn,
                query=query,
                author=author,
                author_id=author_id,
                channel_id=channel_id,
                after=after,
                before=before,
                reaction=reaction,
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
                reaction=reaction,
                visible_channel_ids=g.visible_channel_ids,
            )
            for row in results:
                if g.merge_posts:
                    # search_messages computes preceding_count in messages,
                    # inline and carefully optimised (see its docstring). With
                    # merging on that count is in the wrong unit *and* needs
                    # group-head resolution, so it's recomputed here per row
                    # rather than growing a second variant of that query --
                    # bounded by the page size (25 by default) and only paid
                    # when a mod has actually enabled merging. Folding it back
                    # into the search SQL is a worthwhile optimisation if
                    # search latency ever shows up on a merged install.
                    preceding = await queries.count_posts_before_message(
                        conn,
                        thread_id=row["thread_id"],
                        channel_id=row["channel_id"],
                        posted_at=row["posted_at"],
                        message_id=row["id"],
                        merged=True,
                    )
                else:
                    preceding = row["preceding_count"]
                row["page"] = page_number_for_offset(preceding, page_size=g.posts_per_page)

    total_pages = pagination.total_pages(total, page_size=g.posts_per_page)

    return render_template(
        "search_results.html",
        query=query,
        results=results,
        total=total,
        page=page,
        total_pages=total_pages,
        page_url=lambda n: _url_with(page=n),
        jump_action=url_for("search.search"),
        author_id=author_id,
        author_display_name=author_display_name,
        channel_id=channel_id,
        channel_choices=channel_choices,
        clear_author_url=_url_with(author_id=None, page=None) if author_id is not None else None,
    )
