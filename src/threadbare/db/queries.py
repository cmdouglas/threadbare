"""Read-only queries for displaying mirrored content, as opposed to
sync_worker/repository.py which is scoped to the sync worker's own writes.
The forum web app (ROADMAP.md §4) will need many more read-only queries
(board index, pagination, search) that don't belong under sync_worker/
either — this module is where those grow from.
"""

from collections.abc import Iterable
from datetime import datetime

import psycopg

from threadbare.channel_types import CATEGORY, NON_CONTENT_TYPES, STAGE_VOICE, VOICE
from threadbare.pagination import DEFAULT_PAGE_SIZE

_MESSAGE_COLUMNS_SQL = """
    m.id, m.channel_id, m.thread_id, m.author_id, m.content,
    m.reply_to_id, m.posted_at, m.edited_at, m.type, u.display_name AS author_display_name,
    u.avatar_hash AS author_avatar_hash, u.is_bot AS author_is_bot,
    (SELECT r.color FROM roles r WHERE r.id = ANY(u.role_ids) AND r.color != 0
     ORDER BY r.position DESC LIMIT 1) AS author_role_color
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


async def get_attachments_for_message(conn: psycopg.AsyncConnection, message_id: int) -> list[dict]:
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
                   author_url, footer_text, image_url, thumbnail_url, video_url, fields
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
    one query for the whole batch of boards, not a per-board round trip.
    Boards with zero messages are absent from the query result and
    left-filled here with a zero-valued aggregate.

    The "last post" half is deliberately LATERAL-joined per board rather
    than a window function over every matching row (a real gap found and
    fixed against a genuinely million-row board: a row_number() OVER
    (PARTITION BY board_id ORDER BY posted_at DESC, id DESC) forces Postgres
    to sort the *entire* combined direct+thread row set before it can take
    each board's top row, even though only one row per board is ever kept --
    EXPLAIN ANALYZE showed a 1,000,000-row external merge sort dominating
    the query's cost). A LATERAL subquery per board instead asks "what's
    this board's single latest direct message" and "what's this board's
    single latest thread message" directly off messages_channel_id_
    posted_at_idx / messages_thread_id_posted_at_idx in ORDER BY ... DESC
    LIMIT 1 form -- an index scan bounded by the number of *threads* a board
    has (cheap) rather than a sort bounded by its total *message* count. The
    exact-count half still has to touch every matching row (no index avoids
    that), so it stays a GROUP BY, just no longer forced into the same sort
    the ranking used to need.
    """
    channel_ids = list(channel_ids)
    if not channel_ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT b.board_id,
                   coalesce(c.post_count, 0) AS post_count,
                   top.id AS last_message_id,
                   top.posted_at AS last_posted_at,
                   top.author_id AS last_author_id
            FROM unnest(%(channel_ids)s::bigint[]) AS b(board_id)
            LEFT JOIN (
                SELECT board_id, count(*) AS post_count FROM (
                    SELECT channel_id AS board_id FROM messages
                    WHERE channel_id = ANY(%(channel_ids)s)
                    UNION ALL
                    SELECT t.parent_channel_id AS board_id
                    FROM messages m JOIN threads t ON t.id = m.thread_id
                    WHERE t.parent_channel_id = ANY(%(channel_ids)s)
                ) counted
                GROUP BY board_id
            ) c ON c.board_id = b.board_id
            LEFT JOIN LATERAL (
                SELECT id, posted_at, author_id FROM (
                    (SELECT id, posted_at, author_id FROM messages
                     WHERE channel_id = b.board_id
                     ORDER BY posted_at DESC, id DESC LIMIT 1)
                    UNION ALL
                    (SELECT m.id, m.posted_at, m.author_id
                     FROM threads t
                     JOIN LATERAL (
                         SELECT id, posted_at, author_id FROM messages
                         WHERE thread_id = t.id
                         ORDER BY posted_at DESC, id DESC LIMIT 1
                     ) m ON true
                     WHERE t.parent_channel_id = b.board_id
                     ORDER BY m.posted_at DESC, m.id DESC LIMIT 1)
                ) candidates
                ORDER BY posted_at DESC, id DESC
                LIMIT 1
            ) top ON true
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
    total: int,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """One page of a topic/board's messages, in the same column shape as
    get_message_for_render so a page's rows drop straight into
    render_message_for_display. since/until scope a weekly pseudo-topic to
    its window; otherwise this is the whole container.

    `total` -- the same count every caller already has from
    count_messages_before, to compute total_pages for its pager -- decides
    which end of the (posted_at, id) index to walk from: whichever side has
    fewer rows to skip. A plain ORDER BY ... OFFSET forward from page 1 is
    O(offset): fine near the front, but a real, measured ~850ms nested-loop
    skip for the last page of a 1,000,000-row board (RESOLVED_ISSUES.md).
    Walking backward (ORDER BY ... DESC, then reversing the fetched rows)
    for back-half pages turns that specific worst case into an O(page_size)
    fetch -- offset-from-end 0 for the very last page. Pages near the middle
    of a very large container are still O(min(offset-from-start,
    offset-from-end)): roughly half the previous cost, not eliminated --
    exact, arbitrary-page jumps can't be made cheap without a denormalized
    per-row rank, which is out of scope here (ROADMAP.md's keyset-cursor
    backlog item covers the sequential next/prev case this doesn't).
    """
    assert (thread_id is None) != (channel_id is None)
    conditions = [
        "m.thread_id = %(thread_id)s" if thread_id is not None else "m.channel_id = %(channel_id)s"
    ]
    params: dict = {"thread_id": thread_id, "channel_id": channel_id}
    if since is not None:
        conditions.append("m.posted_at >= %(since)s")
        params["since"] = since
    if until is not None:
        conditions.append("m.posted_at < %(until)s")
        params["until"] = until

    start = (page - 1) * page_size
    end = min(page * page_size, total)
    limit = end - start
    if limit <= 0:
        return []
    trailing = total - end

    fetch_backward = trailing < start
    params["limit"] = limit
    params["offset"] = trailing if fetch_backward else start
    order_sql = "m.posted_at DESC, m.id DESC" if fetch_backward else "m.posted_at, m.id"

    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT {_MESSAGE_COLUMNS_SQL}
            FROM messages m
            JOIN users u ON u.id = m.author_id
            WHERE {" AND ".join(conditions)}
            ORDER BY {order_sql}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        rows = await cur.fetchall()

    if fetch_backward:
        rows.reverse()
    return rows


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


def _visibility_clause(prefix: str = "") -> str:
    """'Legacy OR enrolled+visible': a content channel is readable if it's
    indexed and either (a) globally public (today's unchanged behavior for
    a non-enrolled channel) or (b) enrolled in the per-user visibility
    system (DESIGN.md §7 Phase 2) AND present in the requester's freshly
    computed visible_channel_ids (web/authz.py::resolve_visible_channel_ids).
    A non-enrolled channel (the default) can only ever pass via (a) --
    structurally identical to pre-enrollment behavior. `prefix` targets
    either an aliased join column (search/post-history queries alias
    channels as `c`) or the bare column name (get_boards_and_categories
    selects directly from `channels`, no alias). Every caller must supply a
    visible_channel_ids param -- confirmed directly against Postgres that
    `id = ANY(%(ids)s)` with an empty list evaluates to false for every
    row, no cast/guard needed.
    """
    return f"""
        {prefix}indexed = true
        AND (
            {prefix}is_public = true
            OR ({prefix}visibility_enrolled = true AND {prefix}id = ANY(%(visible_channel_ids)s))
        )
    """


# Shared by search_messages/count_search_results -- explicit ::type casts
# are needed so Postgres can type-check the "IS NULL OR ..." branch even
# when the parameter itself is None.
_SEARCH_WHERE_SQL = f"""
    m.tsv @@ websearch_to_tsquery('english', %(q)s)
    AND {_visibility_clause("c.")}
    AND (%(author)s::text IS NULL OR u.display_name ILIKE %(author)s)
    AND (%(author_id)s::bigint IS NULL OR m.author_id = %(author_id)s)
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
    author_id: int | None,
    channel_id: int | None,
    after: datetime | None,
    before: datetime | None,
    visible_channel_ids: Iterable[int],
) -> dict:
    return {
        "q": query,
        "author": f"%{author}%" if author else None,
        "author_id": author_id,
        "channel_id": channel_id,
        "after": after,
        "before": before,
        "visible_channel_ids": list(visible_channel_ids),
    }


async def search_messages(
    conn: psycopg.AsyncConnection,
    *,
    query: str,
    author: str | None = None,
    author_id: int | None = None,
    channel_id: int | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    visible_channel_ids: Iterable[int],
) -> list[dict]:
    """Full-text search via websearch_to_tsquery (Google-style syntax,
    never raises on malformed input, unlike to_tsquery). Each row's
    preceding_count is the same "how many messages precede this one in its
    container" primitive count_messages_before uses, computed inline so
    results link into the right page of the right topic/board rather than
    an isolated snippet (DESIGN.md §5.4).

    preceding_count is deliberately *not* a single always-forward correlated
    subquery per row anymore (a real gap found and fixed: 25 independent
    O(container size) scans, ~988,000 rows each, ~2.2s measured for a search
    whose hits sit deep in a channel's history -- RESOLVED_ISSUES.md). Three
    changes, the first two mirroring get_messages_page's nearest-end fix:

    1. Per-container stats (min/max posted_at, exact row count) are computed
       once per *distinct* container among this page's matches, not once per
       matched row -- a real win whenever hits cluster in the same
       channel/thread, which they usually do for a specific search term.
       Exact count is a genuine COUNT(*) scan even for threads, not
       threads.message_count -- that field comes straight from Discord's own
       (documented-approximate) thread metadata, not a count of what's
       actually mirrored locally, so it isn't trustworthy as the exact total
       this math depends on.
    2. Each row then counts whichever side of its (posted_at, id) boundary
       is likely smaller (via the min/max straddle) -- "preceding" if it's
       closer to the container's start, else "following", with
       preceding_count derived as total - 1 - following when going
       backward. Still O(n) in the worst case for a container that only
       showed up once, but no longer the *guaranteed* worst case for every
       hit regardless of where it actually sits.
    3. The boundary conditions use `m2.thread_id = matches.thread_id` (or
       `m2.channel_id = matches.channel_id` for a direct message) rather
       than the `IS NOT DISTINCT FROM` this used before -- EXPLAIN ANALYZE
       showed Postgres falling back to a full Seq Scan for that comparison
       even with an otherwise-matching composite index, because `IS NOT
       DISTINCT FROM` isn't sargable the way plain equality is. A message's
       channel_id/thread_id are mutually exclusive by messages_container_
       check (schema migration 0001), so `channel_id = X` alone already
       implies `thread_id IS NULL` -- no need to say so, and saying so
       anyway (an earlier draft of this fix did) was *also* enough to defeat
       the planner's selectivity estimate into another accidental Seq Scan.

    One real cost remains, measured and left as-is rather than papered over:
    the per-container COUNT(*) in step 1 is a genuine full scan of that
    container when it isn't small -- fine for an ordinary channel, but for a
    search whose hits cluster in a channel that itself holds most of the
    table (exactly ROADMAP.md's "million-message channel" scenario), that
    scan alone measured ~140-220ms. Nothing shy of a maintained per-channel
    message counter (out of scope here -- there's no local equivalent of
    threads.message_count for channels, and adding one is a sync-worker
    write-path change, not a query change) removes this; DESIGN.md §10
    tracks it as the narrowed, honest remainder of this gap.
    """
    params = {
        **_search_params(
            query=query,
            author=author,
            author_id=author_id,
            channel_id=channel_id,
            after=after,
            before=before,
            visible_channel_ids=visible_channel_ids,
        ),
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            WITH matches AS (
                SELECT m.id, m.channel_id, m.thread_id, m.posted_at, m.author_id,
                       u.display_name AS author_display_name,
                       ts_headline('english', m.content, websearch_to_tsquery('english', %(q)s),
                                    'MaxFragments=1,MaxWords=35,MinWords=15') AS snippet,
                       ts_rank(m.tsv, websearch_to_tsquery('english', %(q)s)) AS rank
                {_SEARCH_FROM_SQL}
                WHERE {_SEARCH_WHERE_SQL}
                ORDER BY rank DESC, m.posted_at DESC
                LIMIT %(limit)s OFFSET %(offset)s
            ),
            thread_ids AS (
                SELECT DISTINCT thread_id FROM matches WHERE thread_id IS NOT NULL
            ),
            channel_ids AS (
                SELECT DISTINCT channel_id FROM matches WHERE thread_id IS NULL
            ),
            thread_stats AS (
                SELECT t.thread_id AS container_key, true AS is_thread,
                       mn.posted_at AS min_t, mx.posted_at AS max_t, tot.total
                FROM thread_ids t
                JOIN LATERAL (
                    SELECT posted_at FROM messages
                    WHERE thread_id = t.thread_id ORDER BY posted_at ASC, id ASC LIMIT 1
                ) mn ON true
                JOIN LATERAL (
                    SELECT posted_at FROM messages
                    WHERE thread_id = t.thread_id ORDER BY posted_at DESC, id DESC LIMIT 1
                ) mx ON true
                JOIN LATERAL (
                    SELECT count(*) AS total FROM messages WHERE thread_id = t.thread_id
                ) tot ON true
            ),
            channel_stats AS (
                SELECT c.channel_id AS container_key, false AS is_thread,
                       mn.posted_at AS min_t, mx.posted_at AS max_t, tot.total
                FROM channel_ids c
                JOIN LATERAL (
                    SELECT posted_at FROM messages
                    WHERE channel_id = c.channel_id
                    ORDER BY posted_at ASC, id ASC LIMIT 1
                ) mn ON true
                JOIN LATERAL (
                    SELECT posted_at FROM messages
                    WHERE channel_id = c.channel_id
                    ORDER BY posted_at DESC, id DESC LIMIT 1
                ) mx ON true
                JOIN LATERAL (
                    SELECT count(*) AS total FROM messages
                    WHERE channel_id = c.channel_id
                ) tot ON true
            ),
            stats AS (
                SELECT * FROM thread_stats
                UNION ALL
                SELECT * FROM channel_stats
            )
            SELECT matches.id, matches.channel_id, matches.thread_id, matches.posted_at,
                   matches.author_id, matches.author_display_name, matches.snippet,
                   CASE WHEN matches.thread_id IS NOT NULL THEN
                     CASE
                       WHEN (matches.posted_at - stats.min_t) <= (stats.max_t - matches.posted_at)
                       THEN (SELECT count(*) FROM messages m2
                             WHERE m2.thread_id = matches.thread_id
                               AND (m2.posted_at, m2.id) < (matches.posted_at, matches.id))
                       ELSE stats.total - 1 - (SELECT count(*) FROM messages m2
                             WHERE m2.thread_id = matches.thread_id
                               AND (m2.posted_at, m2.id) > (matches.posted_at, matches.id))
                     END
                   ELSE
                     CASE
                       WHEN (matches.posted_at - stats.min_t) <= (stats.max_t - matches.posted_at)
                       THEN (SELECT count(*) FROM messages m2
                             WHERE m2.channel_id = matches.channel_id
                               AND (m2.posted_at, m2.id) < (matches.posted_at, matches.id))
                       ELSE stats.total - 1 - (SELECT count(*) FROM messages m2
                             WHERE m2.channel_id = matches.channel_id
                               AND (m2.posted_at, m2.id) > (matches.posted_at, matches.id))
                     END
                   END AS preceding_count
            FROM matches
            JOIN stats ON stats.container_key = COALESCE(matches.thread_id, matches.channel_id)
                       AND stats.is_thread = (matches.thread_id IS NOT NULL)
            ORDER BY matches.rank DESC, matches.posted_at DESC
            """,
            params,
        )
        return await cur.fetchall()


async def count_search_results(
    conn: psycopg.AsyncConnection,
    *,
    query: str,
    author: str | None = None,
    author_id: int | None = None,
    channel_id: int | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    visible_channel_ids: Iterable[int],
) -> int:
    params = _search_params(
        query=query,
        author=author,
        author_id=author_id,
        channel_id=channel_id,
        after=after,
        before=before,
        visible_channel_ids=visible_channel_ids,
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
            "SELECT id, display_name, avatar_hash, is_bot, role_ids FROM users WHERE id = %s",
            (user_id,),
        )
        return await cur.fetchone()


async def get_roles_by_ids(conn: psycopg.AsyncConnection, role_ids: list[int]) -> list[dict]:
    if not role_ids:
        return []
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, name, color, position FROM roles WHERE id = ANY(%s) ORDER BY position DESC",
            (role_ids,),
        )
        return await cur.fetchall()


async def get_guild(conn: psycopg.AsyncConnection, guild_id: int) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, name, icon FROM guilds WHERE id = %s", (guild_id,))
        return await cur.fetchone()


async def get_base_permissions(
    conn: psycopg.AsyncConnection, *, guild_id: int, role_ids: list[int]
) -> int:
    """@everyone's permissions OR'd with every role in role_ids. @everyone's
    row id equals guild_id (Discord's own convention -- discover_roles's
    guild.fetch_roles() always includes guild.default_role, whose id is the
    guild's id), and a member's stored role_ids never includes it, so
    guild_id is added explicitly here rather than assumed present.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT COALESCE(bit_or(permissions), 0) AS base_permissions
            FROM roles
            WHERE guild_id = %(guild_id)s AND id = ANY(%(ids)s)
            """,
            {"guild_id": guild_id, "ids": [guild_id, *role_ids]},
        )
        row = await cur.fetchone()
    return row["base_permissions"]


async def get_visibility_channels(conn: psycopg.AsyncConnection, *, guild_id: int) -> list[dict]:
    """id, parent_id for every real content channel in the guild (excludes
    categories/voice/stage-voice) -- the full population
    channel_visibility.compute_visible_channel_ids walks. Deliberately
    unfiltered by is_public/indexed: this computes genuine per-identity
    visibility, not "already publicly indexed channels only" -- a role can
    regain access @everyone can't see.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, parent_id FROM channels
            WHERE guild_id = %(guild_id)s AND type != ALL(%(non_content)s)
            """,
            {"guild_id": guild_id, "non_content": list(NON_CONTENT_TYPES)},
        )
        return await cur.fetchall()


async def get_channel_role_overwrites(
    conn: psycopg.AsyncConnection, *, channel_ids: list[int], role_ids: list[int]
) -> list[dict]:
    """channel_ids should cover both the content channels being evaluated
    and their distinct parent category ids in one call, avoiding N+1 per
    channel. role_ids should already include everyone_role_id (guild_id).
    """
    if not channel_ids or not role_ids:
        return []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT channel_id, role_id, allow, deny
            FROM channel_role_overwrites
            WHERE channel_id = ANY(%(channel_ids)s) AND role_id = ANY(%(role_ids)s)
            """,
            {"channel_ids": channel_ids, "role_ids": role_ids},
        )
        return await cur.fetchall()


async def get_channel_member_overwrites(
    conn: psycopg.AsyncConnection, *, channel_ids: list[int], user_id: int
) -> list[dict]:
    if not channel_ids:
        return []
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT channel_id, allow, deny
            FROM channel_member_overwrites
            WHERE channel_id = ANY(%(channel_ids)s) AND user_id = %(user_id)s
            """,
            {"channel_ids": channel_ids, "user_id": user_id},
        )
        return await cur.fetchall()


async def get_post_count_for_user(
    conn: psycopg.AsyncConnection, user_id: int, *, visible_channel_ids: Iterable[int]
) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT count(*) AS n
            {_SEARCH_FROM_SQL}
            WHERE m.author_id = %(user_id)s AND {_visibility_clause("c.")}
            """,
            {"user_id": user_id, "visible_channel_ids": list(visible_channel_ids)},
        )
        row = await cur.fetchone()
    return row["n"]


async def get_recent_posts_for_user(
    conn: psycopg.AsyncConnection,
    user_id: int,
    *,
    visible_channel_ids: Iterable[int],
    limit: int = 10,
) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT {_MESSAGE_COLUMNS_SQL}
            {_SEARCH_FROM_SQL}
            WHERE m.author_id = %(user_id)s AND {_visibility_clause("c.")}
            ORDER BY m.posted_at DESC, m.id DESC
            LIMIT %(limit)s
            """,
            {"user_id": user_id, "limit": limit, "visible_channel_ids": list(visible_channel_ids)},
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
            SELECT id, guild_id, parent_id, type, name, position, topic,
                   is_public, indexed, visibility_enrolled
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


async def get_boards_and_categories(
    conn: psycopg.AsyncConnection, guild_id: int, *, visible_channel_ids: Iterable[int]
) -> list[dict]:
    """Categories (always -- they're grouping metadata, shown regardless of
    what's under them) plus boards the requester can currently see, for the
    board index / web.board_tree.group_channels_by_category: a board passes
    if it's indexed and either globally public (today's unchanged behavior
    for a non-enrolled board) or enrolled in the per-user visibility system
    and present in visible_channel_ids (see _visibility_clause). A channel
    that stops being public already has its content purged at the source
    (sync_worker); this additionally keeps its now-content-less row from
    ever appearing as a browsable board. Voice/stage-voice channels are
    excluded outright even if a stale row has them public+indexed (a stated
    non-goal, DESIGN.md §2) -- discovery no longer creates such rows, but
    this guards an already-deployed install with one from before that fix.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT id, parent_id, type, name, position, topic
            FROM channels
            WHERE guild_id = %(guild_id)s
              AND type != ALL(%(non_boardable)s)
              AND (
                type = %(category)s
                OR ({_visibility_clause("")})
              )
            ORDER BY position
            """,
            {
                "guild_id": guild_id,
                "category": CATEGORY,
                "non_boardable": [VOICE, STAGE_VOICE],
                "visible_channel_ids": list(visible_channel_ids),
            },
        )
        return await cur.fetchall()


async def mark_read(
    conn: psycopg.AsyncConnection,
    *,
    user_id: int,
    channel_id: int | None = None,
    thread_id: int | None = None,
    message_id: int,
    posted_at: datetime,
) -> None:
    """Advance user_id's read marker for this board/topic to
    (posted_at, message_id) -- the same (posted_at, id) ordering key every
    other query already sorts and compares on, rather than a bare timestamp
    or a raw snowflake id alone. Forward-only: viewing an older page after
    already reading further never rewinds progress, so the update only
    takes effect when the new position actually sorts later than what's
    stored (row-wise tuple comparison, not just posted_at, so two messages
    landing in the same instant still resolve by id).
    """
    assert (channel_id is None) != (thread_id is None)
    container_column = "channel_id" if channel_id is not None else "thread_id"
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            INSERT INTO read_markers
                (user_id, {container_column}, last_read_message_id, last_read_posted_at)
            VALUES (%(user_id)s, %(container_id)s, %(message_id)s, %(posted_at)s)
            ON CONFLICT (user_id, {container_column}) WHERE {container_column} IS NOT NULL
            DO UPDATE SET
                last_read_message_id = EXCLUDED.last_read_message_id,
                last_read_posted_at = EXCLUDED.last_read_posted_at,
                updated_at = now()
            WHERE (EXCLUDED.last_read_posted_at, EXCLUDED.last_read_message_id)
                > (read_markers.last_read_posted_at, read_markers.last_read_message_id)
            """,
            {
                "user_id": user_id,
                "container_id": channel_id if channel_id is not None else thread_id,
                "message_id": message_id,
                "posted_at": posted_at,
            },
        )


async def get_read_marker(
    conn: psycopg.AsyncConnection,
    *,
    user_id: int,
    channel_id: int | None = None,
    thread_id: int | None = None,
) -> dict | None:
    assert (channel_id is None) != (thread_id is None)
    condition = (
        "channel_id = %(container_id)s"
        if channel_id is not None
        else "thread_id = %(container_id)s"
    )
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT last_read_message_id, last_read_posted_at
            FROM read_markers
            WHERE user_id = %(user_id)s AND {condition}
            """,
            {
                "user_id": user_id,
                "container_id": channel_id if channel_id is not None else thread_id,
            },
        )
        return await cur.fetchone()


async def get_read_markers(
    conn: psycopg.AsyncConnection,
    *,
    user_id: int,
    channel_ids: Iterable[int],
    thread_ids: Iterable[int],
) -> dict[int, dict]:
    """Batched sibling of get_read_marker for a listing page (board index's
    boards, a board's topic list) that needs every visible container's
    marker in one round trip rather than one query per row. Keyed by
    channel_id or thread_id -- callers already know which container kind
    each id is, same UNION ALL idiom search_messages already uses to
    combine the channel/thread branches of one mutually-exclusive column.
    """
    channel_ids = list(channel_ids)
    thread_ids = list(thread_ids)
    if not channel_ids and not thread_ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT channel_id AS container_id, last_read_message_id, last_read_posted_at
            FROM read_markers
            WHERE user_id = %(user_id)s AND channel_id = ANY(%(channel_ids)s)
            UNION ALL
            SELECT thread_id AS container_id, last_read_message_id, last_read_posted_at
            FROM read_markers
            WHERE user_id = %(user_id)s AND thread_id = ANY(%(thread_ids)s)
            """,
            {"user_id": user_id, "channel_ids": channel_ids, "thread_ids": thread_ids},
        )
        rows = await cur.fetchall()
    return {
        row["container_id"]: {
            "last_read_message_id": row["last_read_message_id"],
            "last_read_posted_at": row["last_read_posted_at"],
        }
        for row in rows
    }


async def count_unread(
    conn: psycopg.AsyncConnection,
    *,
    marker: dict | None,
    total: int,
    channel_id: int | None = None,
    thread_id: int | None = None,
) -> int:
    """Number of unread messages in this container, given its own
    already-known total (every real caller has this on hand already --
    get_messages_page's own total kwarg, or a dedicated
    count_messages_before call, so this never runs a second full
    COUNT(*)) and its already-fetched read marker (get_read_marker for a
    single container, get_read_markers for a batch of listing rows -- the
    marker is a parameter here rather than fetched internally specifically
    so a listing page's already-batched markers don't get refetched one
    row at a time). No marker at all means nothing's ever been read, so
    everything in the container is unread. Otherwise reuses
    count_messages_before's (posted_at, id)-tuple comparison (already built
    for permalinks/jump-to-date) to count how many messages are already
    read -- the same "how many precede this point" primitive
    board.board_continuous_jump_to_unread/topic.topic_jump_to_unread use to
    find the first unread post, just subtracted from total instead of
    turned into a page number.
    """
    if marker is None:
        return total
    assert (channel_id is None) != (thread_id is None)
    preceding = await count_messages_before(
        conn,
        channel_id=channel_id,
        thread_id=thread_id,
        before=(marker["last_read_posted_at"], marker["last_read_message_id"]),
    )
    return max(total - (preceding + 1), 0)


async def has_unread(
    conn: psycopg.AsyncConnection,
    *,
    user_id: int,
    channel_id: int | None = None,
    thread_id: int | None = None,
    total: int,
) -> bool:
    """Whether anything in this container is unread for user_id -- the
    single-container, fetch-the-marker-myself sibling of count_unread for
    a content page's own show/hide check, where there's no already-batched
    marker to reuse.
    """
    assert (channel_id is None) != (thread_id is None)
    marker = await get_read_marker(
        conn, user_id=user_id, channel_id=channel_id, thread_id=thread_id
    )
    unread = await count_unread(
        conn, marker=marker, total=total, channel_id=channel_id, thread_id=thread_id
    )
    return unread > 0
