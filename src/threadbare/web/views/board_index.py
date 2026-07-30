from flask import Blueprint, current_app, g, render_template, session, url_for

from threadbare import pagination
from threadbare.db import queries
from threadbare.web.board_tree import board_view_mode, group_channels_by_category

bp = Blueprint("board_index", __name__)


@bp.route("/")
async def board_index():
    settings = current_app.config["SETTINGS"]
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        rows = await queries.get_boards_and_categories(
            conn, settings.discord_guild_id, visible_channel_ids=g.visible_channel_ids
        )
        groups = group_channels_by_category(rows)

        board_ids = [row["id"] for group in groups for row in group["boards"]]
        aggregates = await queries.get_board_post_aggregates(conn, board_ids)
        author_ids = {a["last_author_id"] for a in aggregates.values() if a["last_author_id"]}
        authors = await queries.resolve_users(conn, author_ids)

        # One query per board rather than a batched ANY(%s) variant -- typical
        # servers have a handful to a few dozen channels, not thousands, so
        # this is a fine trade against building batch-count query variants
        # nothing else would ever need (ROADMAP.md already reasoned the whole
        # feature is only worth it at this scale).
        # channel-only message totals, not group_channels_by_category's or
        # get_board_post_aggregates's combined channel+thread view -- a
        # freeform board's read marker is set by
        # board.board_continuous_page/board_week_page, which only ever see
        # this channel's own direct messages, never its threads' content.
        # Comparing that marker against anything thread-inclusive would be
        # comparing two different scopes.
        board_message_totals: dict[int, int] = {}
        board_total_pages: dict[int, int] = {}
        board_jump_action: dict[int, str] = {}
        # Built explicitly rather than derived from board_message_totals' keys:
        # the two happened to coincide, but that was an implicit invariant
        # spanning twenty lines, reached through a dict whose name says nothing
        # about view mode.
        freeform_board_ids: list[int] = []
        for group in groups:
            for board in group["boards"]:
                if board_view_mode(board) == "freeform":
                    # Two totals, in two different units, and they are not
                    # interchangeable:
                    #
                    # `total` paginates, so it must match what
                    # board.board_continuous_page counts for the same board --
                    # posts when merging is on. These are two separate pagers
                    # over the same content, and if only one counts posts the
                    # index advertises pages the board doesn't have.
                    #
                    # board_message_totals feeds count_unread below, which
                    # takes a *message* count -- read tracking stays in
                    # messages everywhere (see topic.topic_page), so that one
                    # is always unmerged. Merging them would report "12 new
                    # messages" computed against a post count.
                    total = await queries.count_messages_before(
                        conn, channel_id=board["id"], merged=g.merge_posts
                    )
                    board_message_totals[board["id"]] = (
                        total
                        if not g.merge_posts
                        else await queries.count_messages_before(conn, channel_id=board["id"])
                    )
                    freeform_board_ids.append(board["id"])
                    board_jump_action[board["id"]] = url_for(
                        "board.board_continuous_jump_to_page", channel_id=board["id"]
                    )
                else:
                    total = await queries.count_topics_for_board(conn, board["id"])
                    board_jump_action[board["id"]] = url_for(
                        "board.board_topics", channel_id=board["id"]
                    )
                # Same page size for both arms: a topics_only board's topic list
                # is paginated by board.board_topics, which honours the reader's
                # posts_per_page preference like every other listing.
                board_total_pages[board["id"]] = pagination.total_pages(
                    total, page_size=g.posts_per_page
                )

        # Unread state only covers freeform boards here: a topics_only
        # board never gets a channel-level marker at all -- reading happens
        # per-topic (topic.topic_page) -- so accurate unread state for one
        # would need every one of its threads' read state aggregated up,
        # which is real added scope left for later rather than showing
        # something that would just always read "unread".
        markers = await queries.get_read_markers(
            conn, user_id=session["user_id"], channel_ids=freeform_board_ids, thread_ids=[]
        )
        board_unread_counts = {
            board_id: await queries.count_unread(
                conn,
                marker=markers.get(board_id),
                total=board_message_totals[board_id],
                channel_id=board_id,
            )
            for board_id in freeform_board_ids
        }
        unread_boards = {board_id: count > 0 for board_id, count in board_unread_counts.items()}
        # Shown only for a board that's genuinely partially read: no marker
        # at all means nothing's ever been read, where "jump to first
        # unread" and "open at page 1" land on the same place (a no-op
        # duplicate of the board name link); a marker with nothing unread
        # beyond it means there's nowhere left to jump to.
        board_jump_to_unread_action = {
            board_id: url_for("board.board_continuous_jump_to_unread", channel_id=board_id)
            for board_id in freeform_board_ids
            if board_id in markers and unread_boards.get(board_id)
        }

    return render_template(
        "board_index.html",
        groups=groups,
        aggregates=aggregates,
        authors=authors,
        board_total_pages=board_total_pages,
        board_jump_action=board_jump_action,
        unread_boards=unread_boards,
        board_unread_counts=board_unread_counts,
        board_jump_to_unread_action=board_jump_to_unread_action,
        visited_boards=set(markers),
    )
