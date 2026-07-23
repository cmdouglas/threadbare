"""Read-only queries for displaying mirrored content, as opposed to
sync_worker/repository.py which is scoped to the sync worker's own writes.
The forum web app (ROADMAP.md §4) will need many more read-only queries
(board index, pagination, search) that don't belong under sync_worker/
either — this module is where those grow from.
"""

from collections.abc import Iterable
from datetime import datetime

import psycopg

from threadbare.channel_types import CATEGORY
from threadbare.pagination import DEFAULT_PAGE_SIZE

_MESSAGE_COLUMNS_SQL = """
    m.id, m.channel_id, m.thread_id, m.author_id, m.content,
    m.reply_to_id, m.posted_at, m.edited_at, m.type, u.display_name AS author_display_name,
    u.avatar_hash AS author_avatar_hash
"""


async def get_message_for_render(conn: psycopg.AsyncConnection, message_id: int) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT {_MESSAGE_COLUMNS_SQL}
            FROM messages m
            JOIN users u ON u.id = m.author_id
            WHERE m.id = %s
            """,
            (message_id,),
        )
        return await cur.fetchone()


async def get_attachments_for_message(
    conn: psycopg.AsyncConnection, message_id: int
) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, filename, content_type, size, cached_url, url_expires_at
            FROM attachments
            WHERE message_id = %s
            ORDER BY id
            """,
            (message_id,),
        )
        return await cur.fetchall()


async def get_embeds_for_message(conn: psycopg.AsyncConnection, message_id: int) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT position, type, title, description, url, color, author_name,
                   author_url, footer_text, image_url, thumbnail_url, fields
            FROM embeds
            WHERE message_id = %s
            ORDER BY position
            """,
            (message_id,),
        )
        return await cur.fetchall()


async def get_reactions_for_message(
    conn: psycopg.AsyncConnection, message_id: int
) -> list[tuple[str, int]]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT emoji, count FROM reactions WHERE message_id = %s ORDER BY emoji",
            (message_id,),
        )
        rows = await cur.fetchall()
    return [(row["emoji"], row["count"]) for row in rows]


async def resolve_users(conn: psycopg.AsyncConnection, ids: Iterable[int]) -> dict[int, str]:
    ids = list(ids)
    if not ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, display_name FROM users WHERE id = ANY(%s)", (ids,))
        rows = await cur.fetchall()
    return {row["id"]: row["display_name"] for row in rows}


async def resolve_channels(conn: psycopg.AsyncConnection, ids: Iterable[int]) -> dict[int, str]:
    ids = list(ids)
    if not ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, name FROM channels WHERE id = ANY(%s)", (ids,))
        rows = await cur.fetchall()
    return {row["id"]: row["name"] for row in rows}


async def count_messages_before(
    conn: psycopg.AsyncConnection,
    *,
    thread_id: int | None = None,
    channel_id: int | None = None,
    before: tuple[datetime, int] | datetime | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> int:
    """The shared "how many messages precede this point" primitive behind
    permalinks (before=(posted_at, id) of a specific message), jump-to-date
    (before=a bare date), first/prev/next/last (before=None -> total count),
    and weekly pseudo-topics (since/until window). Exactly one of
    thread_id/channel_id must be set, mirroring messages' own
    messages_container_check constraint.
    """
    assert (thread_id is None) != (channel_id is None)
    conditions = [
        "thread_id = %(thread_id)s" if thread_id is not None else "channel_id = %(channel_id)s"
    ]
    params: dict = {"thread_id": thread_id, "channel_id": channel_id}

    if isinstance(before, tuple):
        conditions.append("(posted_at, id) < (%(before_posted_at)s, %(before_id)s)")
        params["before_posted_at"], params["before_id"] = before
    elif before is not None:
        conditions.append("posted_at < %(before)s")
        params["before"] = before

    if since is not None:
        conditions.append("posted_at >= %(since)s")
        params["since"] = since
    if until is not None:
        conditions.append("posted_at < %(until)s")
        params["until"] = until

    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT count(*) AS n FROM messages WHERE " + " AND ".join(conditions), params
        )
        row = await cur.fetchone()
    return row["n"]


_ZERO_AGGREGATE = {
    "post_count": 0,
    "last_message_id": None,
    "last_posted_at": None,
    "last_author_id": None,
}


async def get_board_post_aggregates(
    conn: psycopg.AsyncConnection, channel_ids: Iterable[int]
) -> dict[int, dict]:
    """Post count + last-post (message id/time/author) per board, combining
    a board's own direct messages with every message inside its threads --
    one query for the whole batch of boards via window functions, not a
    per-board round trip. Boards with zero messages are absent from the
    query result and left-filled here with a zero-valued aggregate.
    """
    channel_ids = list(channel_ids)
    if not channel_ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH board_messages AS (
                SELECT channel_id AS board_id, id, posted_at, author_id
                FROM messages
                WHERE channel_id = ANY(%(channel_ids)s)
                UNION ALL
                SELECT t.parent_channel_id AS board_id, m.id, m.posted_at, m.author_id
                FROM messages m
                JOIN threads t ON t.id = m.thread_id
                WHERE t.parent_channel_id = ANY(%(channel_ids)s)
            ),
            ranked AS (
                SELECT *,
                    row_number() OVER (
                        PARTITION BY board_id ORDER BY posted_at DESC, id DESC
                    ) AS rn,
                    count(*) OVER (PARTITION BY board_id) AS post_count
                FROM board_messages
            )
            SELECT board_id, post_count, id AS last_message_id,
                   posted_at AS last_posted_at, author_id AS last_author_id
            FROM ranked
            WHERE rn = 1
            """,
            {"channel_ids": channel_ids},
        )
        rows = await cur.fetchall()
    result = {cid: dict(_ZERO_AGGREGATE) for cid in channel_ids}
    for row in rows:
        result[row["board_id"]] = {
            "post_count": row["post_count"],
            "last_message_id": row["last_message_id"],
            "last_posted_at": row["last_posted_at"],
            "last_author_id": row["last_author_id"],
        }
    return result


async def get_thread_post_aggregates(
    conn: psycopg.AsyncConnection, thread_ids: Iterable[int]
) -> dict[int, dict]:
    """Structural twin of get_board_post_aggregates for a single board's
    topic list -- no UNION needed, thread messages are always thread_id-
    attached directly.
    """
    thread_ids = list(thread_ids)
    if not thread_ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            WITH ranked AS (
                SELECT thread_id, id, posted_at, author_id,
                    row_number() OVER (
                        PARTITION BY thread_id ORDER BY posted_at DESC, id DESC
                    ) AS rn,
                    count(*) OVER (PARTITION BY thread_id) AS post_count
                FROM messages
                WHERE thread_id = ANY(%(thread_ids)s)
            )
            SELECT thread_id, post_count, id AS last_message_id,
                   posted_at AS last_posted_at, author_id AS last_author_id
            FROM ranked
            WHERE rn = 1
            """,
            {"thread_ids": thread_ids},
        )
        rows = await cur.fetchall()
    result = {tid: dict(_ZERO_AGGREGATE) for tid in thread_ids}
    for row in rows:
        result[row["thread_id"]] = {
            "post_count": row["post_count"],
            "last_message_id": row["last_message_id"],
            "last_posted_at": row["last_posted_at"],
            "last_author_id": row["last_author_id"],
        }
    return result


async def count_topics_for_board(conn: psycopg.AsyncConnection, channel_id: int) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT count(*) AS n FROM threads WHERE parent_channel_id = %s", (channel_id,)
        )
        row = await cur.fetchone()
    return row["n"]


async def get_messages_page(
    conn: psycopg.AsyncConnection,
    *,
    thread_id: int | None = None,
    channel_id: int | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """One page of a topic/board's messages, in the same column shape as
    get_message_for_render so a page's rows drop straight into
    render_message_for_display. since/until scope a weekly pseudo-topic to
    its window; otherwise this is the whole container.
    """
    assert (thread_id is None) != (channel_id is None)
    conditions = [
        "m.thread_id = %(thread_id)s" if thread_id is not None else "m.channel_id = %(channel_id)s"
    ]
    params: dict = {
        "thread_id": thread_id,
        "channel_id": channel_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    if since is not None:
        conditions.append("m.posted_at >= %(since)s")
        params["since"] = since
    if until is not None:
        conditions.append("m.posted_at < %(until)s")
        params["until"] = until

    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT {_MESSAGE_COLUMNS_SQL}
            FROM messages m
            JOIN users u ON u.id = m.author_id
            WHERE {" AND ".join(conditions)}
            ORDER BY m.posted_at, m.id
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return await cur.fetchall()


async def get_attachment_by_id(conn: psycopg.AsyncConnection, attachment_id: int) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, message_id, filename, content_type, size, cached_url, url_expires_at
            FROM attachments
            WHERE id = %s
            """,
            (attachment_id,),
        )
        return await cur.fetchone()


async def update_attachment_cache(
    conn: psycopg.AsyncConnection,
    attachment_id: int,
    *,
    cached_url: str,
    url_expires_at: datetime,
) -> None:
    await conn.execute(
        "UPDATE attachments SET cached_url = %s, url_expires_at = %s WHERE id = %s",
        (cached_url, url_expires_at, attachment_id),
    )


# Shared by search_messages/count_search_results -- explicit ::type casts
# are needed so Postgres can type-check the "IS NULL OR ..." branch even
# when the parameter itself is None.
_SEARCH_WHERE_SQL = """
    m.tsv @@ websearch_to_tsquery('english', %(q)s)
    AND c.indexed = true
    AND (%(author)s::text IS NULL OR u.display_name ILIKE %(author)s)
    AND (
        %(channel_id)s::bigint IS NULL
        OR m.channel_id = %(channel_id)s
        OR th.parent_channel_id = %(channel_id)s
    )
    AND (%(after)s::timestamptz IS NULL OR m.posted_at >= %(after)s)
    AND (%(before)s::timestamptz IS NULL OR m.posted_at < %(before)s)
"""

_SEARCH_FROM_SQL = """
    FROM messages m
    JOIN users u ON u.id = m.author_id
    LEFT JOIN threads th ON th.id = m.thread_id
    JOIN channels c ON c.id = COALESCE(m.channel_id, th.parent_channel_id)
"""


def _search_params(
    *,
    query: str,
    author: str | None,
    channel_id: int | None,
    after: datetime | None,
    before: datetime | None,
) -> dict:
    return {
        "q": query,
        "author": f"%{author}%" if author else None,
        "channel_id": channel_id,
        "after": after,
        "before": before,
    }


async def search_messages(
    conn: psycopg.AsyncConnection,
    *,
    query: str,
    author: str | None = None,
    channel_id: int | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[dict]:
    """Full-text search via websearch_to_tsquery (Google-style syntax,
    never raises on malformed input, unlike to_tsquery). Each row's
    preceding_count is the same "how many messages precede this one in its
    container" primitive count_messages_before uses, computed inline so
    results link into the right page of the right topic/board rather than
    an isolated snippet (DESIGN.md §5.4).
    """
    params = {
        **_search_params(
            query=query, author=author, channel_id=channel_id, after=after, before=before
        ),
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT m.id, m.channel_id, m.thread_id, m.posted_at, m.author_id,
                   u.display_name AS author_display_name,
                   ts_headline('english', m.content, websearch_to_tsquery('english', %(q)s),
                                'MaxFragments=1,MaxWords=35,MinWords=15') AS snippet,
                   (SELECT count(*) FROM messages m2
                    WHERE m2.thread_id IS NOT DISTINCT FROM m.thread_id
                      AND m2.channel_id IS NOT DISTINCT FROM m.channel_id
                      AND (m2.posted_at, m2.id) < (m.posted_at, m.id)) AS preceding_count
            {_SEARCH_FROM_SQL}
            WHERE {_SEARCH_WHERE_SQL}
            ORDER BY ts_rank(m.tsv, websearch_to_tsquery('english', %(q)s)) DESC, m.posted_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return await cur.fetchall()


async def count_search_results(
    conn: psycopg.AsyncConnection,
    *,
    query: str,
    author: str | None = None,
    channel_id: int | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
) -> int:
    params = _search_params(
        query=query, author=author, channel_id=channel_id, after=after, before=before
    )
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT count(*) AS n
            {_SEARCH_FROM_SQL}
            WHERE {_SEARCH_WHERE_SQL}
            """,
            params,
        )
        row = await cur.fetchone()
    return row["n"]


async def get_user(conn: psycopg.AsyncConnection, user_id: int) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, display_name, avatar_hash FROM users WHERE id = %s", (user_id,)
        )
        return await cur.fetchone()


async def get_guild(conn: psycopg.AsyncConnection, guild_id: int) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, name, icon FROM guilds WHERE id = %s", (guild_id,))
        return await cur.fetchone()


async def get_post_count_for_user(conn: psycopg.AsyncConnection, user_id: int) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT count(*) AS n
            {_SEARCH_FROM_SQL}
            WHERE m.author_id = %(user_id)s AND c.indexed = true
            """,
            {"user_id": user_id},
        )
        row = await cur.fetchone()
    return row["n"]


async def get_recent_posts_for_user(
    conn: psycopg.AsyncConnection, user_id: int, *, limit: int = 10
) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT {_MESSAGE_COLUMNS_SQL}
            {_SEARCH_FROM_SQL}
            WHERE m.author_id = %(user_id)s AND c.indexed = true
            ORDER BY m.posted_at DESC, m.id DESC
            LIMIT %(limit)s
            """,
            {"user_id": user_id, "limit": limit},
        )
        return await cur.fetchall()


async def get_threads_for_board(
    conn: psycopg.AsyncConnection,
    channel_id: int,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, parent_channel_id, name, archived, created_at, message_count
            FROM threads
            WHERE parent_channel_id = %(channel_id)s
            ORDER BY created_at DESC, id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"channel_id": channel_id, "limit": page_size, "offset": (page - 1) * page_size},
        )
        return await cur.fetchall()


async def get_weeks_for_board(conn: psycopg.AsyncConnection, channel_id: int) -> list[dict]:
    """Weekly pseudo-topic buckets for a freeform channel (ROADMAP.md §4),
    newest first. ISO year/week (matches pseudotopics.week_id_for's UTC ISO
    calendar convention exactly, so ids round-trip through week_bounds()).
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT
                extract(isoyear FROM posted_at)::int AS iso_year,
                extract(week FROM posted_at)::int AS iso_week,
                count(*) AS post_count,
                max(posted_at) AS last_posted_at
            FROM messages
            WHERE channel_id = %s
            GROUP BY iso_year, iso_week
            ORDER BY iso_year DESC, iso_week DESC
            """,
            (channel_id,),
        )
        rows = await cur.fetchall()
    return [
        {
            "week_id": f"{row['iso_year']:04d}-W{row['iso_week']:02d}",
            "post_count": row["post_count"],
            "last_posted_at": row["last_posted_at"],
        }
        for row in rows
    ]


async def get_channel(conn: psycopg.AsyncConnection, channel_id: int) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, guild_id, parent_id, type, name, position, topic
            FROM channels WHERE id = %s
            """,
            (channel_id,),
        )
        return await cur.fetchone()


async def get_thread(conn: psycopg.AsyncConnection, thread_id: int) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, parent_channel_id, name, archived, created_at, message_count
            FROM threads WHERE id = %s
            """,
            (thread_id,),
        )
        return await cur.fetchone()


async def get_boards_and_categories(conn: psycopg.AsyncConnection, guild_id: int) -> list[dict]:
    """Categories (always -- they're grouping metadata, shown regardless of
    what's under them) plus boards that are currently public and indexed,
    for the board index / web.board_tree.group_channels_by_category. A
    channel that stops being public already has its content purged at the
    source (sync_worker); this additionally keeps its now-content-less row
    from ever appearing as a browsable board.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, parent_id, type, name, position
            FROM channels
            WHERE guild_id = %(guild_id)s
              AND (type = %(category)s OR (is_public = true AND indexed = true))
            ORDER BY position
            """,
            {"guild_id": guild_id, "category": CATEGORY},
        )
        return await cur.fetchall()
