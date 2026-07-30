"""The "does this message start a new post" predicate, and the one primitive
that applies it (DESIGN.md §5, consecutive-post merging).

Every caller is regroup_range with different bounds -- the live ingestion path
regroups a batch, a delete regroups the neighbourhood around it, nightly
reconciliation regroups a whole stale channel, and the admin Regroup button
regroups the guild. Keeping the predicate itself in exactly one SQL expression
is the only way those stay consistent; a second hand-written copy for the
single-message case is how they'd drift.

The predicate is deliberately *pairwise-local*: whether a message starts a
post depends only on it and its immediate predecessor in the same container,
never on the run's length or its head. That's what bounds maintenance -- an
insert or delete can only affect the messages immediately around it, so no
mutation ever needs to walk a whole container.
"""

from datetime import datetime, timedelta

import psycopg

# 7 minutes, Discord's own visual grouping window, so the default matches what
# a reader already sees in the client. Overridable per call; the mod-facing
# setting lives in site_settings.merge_gap_seconds (migration 0016).
DEFAULT_GAP_SECONDS = 420

# A message starts a new post when any of these holds. Written against a
# lag() window over the container in (posted_at, id) order -- the same
# ordering key every other query in this codebase sorts and compares on.
#
# The attachment and system-message rules are two-sided on purpose: such a
# message neither joins the previous post nor accepts the next one into its
# own, so it stands alone. A run of three images by one author stays three
# posts, which is the point.
_STARTS_GROUP_SQL = """
    lag(author_id) OVER w IS NULL
    OR lag(author_id) OVER w <> author_id
    OR posted_at - (lag(posted_at) OVER w) > make_interval(secs => %(gap_seconds)s::int)
    OR reply_to_id IS NOT NULL
    OR type <> 0
    OR lag(type) OVER w <> 0
    OR has_attachments
    OR lag(has_attachments) OVER w
"""


async def regroup_range(
    conn: psycopg.AsyncConnection,
    *,
    channel_id: int | None = None,
    thread_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    gap_seconds: int = DEFAULT_GAP_SECONDS,
) -> int:
    """Recompute messages.starts_group for one container, optionally scoped to
    a [since, until) window. Returns the number of rows actually changed.

    Exactly one of channel_id/thread_id must be set, mirroring messages' own
    messages_container_check constraint.

    **The window is read one message wider than it is written.** `scoped`
    reaches back to the last message before `since` so the first message
    *in* the window can see its predecessor; the UPDATE then re-applies the
    bounds so that context row isn't itself rewritten. Without that reach-back
    every regroup boundary would mark its first message a head, sprouting a
    spurious post split at the edge of every batch and every repair sweep.

    Only rows whose flag actually changes are written (IS DISTINCT FROM),
    which is what keeps a gap-threshold tweak from rewriting every row of a
    million-message channel -- a re-run over unchanged history costs reads
    only.
    """
    assert (thread_id is None) != (channel_id is None)
    container_sql = (
        "channel_id = %(channel_id)s" if channel_id is not None else ("thread_id = %(thread_id)s")
    )
    params = {
        "channel_id": channel_id,
        "thread_id": thread_id,
        "since": since,
        "until": until,
        "gap_seconds": gap_seconds,
    }

    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            WITH lower_bound AS (
                SELECT COALESCE(
                    (SELECT posted_at FROM messages
                      WHERE {container_sql}
                        AND %(since)s::timestamptz IS NOT NULL
                        AND posted_at < %(since)s::timestamptz
                      ORDER BY posted_at DESC, id DESC
                      LIMIT 1),
                    '-infinity'::timestamptz
                ) AS ts
            ),
            scoped AS (
                SELECT m.id, m.author_id, m.posted_at, m.reply_to_id, m.type,
                       EXISTS (
                           SELECT 1 FROM attachments a WHERE a.message_id = m.id
                       ) AS has_attachments
                FROM messages m, lower_bound
                WHERE m.{container_sql}
                  AND m.posted_at >= lower_bound.ts
                  AND (%(until)s::timestamptz IS NULL OR m.posted_at < %(until)s::timestamptz)
            ),
            computed AS (
                SELECT id, posted_at, ({_STARTS_GROUP_SQL}) AS starts_group
                FROM scoped
                WINDOW w AS (ORDER BY posted_at, id)
            )
            UPDATE messages m
               SET starts_group = c.starts_group
              FROM computed c
             WHERE m.id = c.id
               AND (%(since)s::timestamptz IS NULL OR c.posted_at >= %(since)s::timestamptz)
               AND m.starts_group IS DISTINCT FROM c.starts_group
            """,
            params,
        )
        return cur.rowcount


async def regroup_around(
    conn: psycopg.AsyncConnection,
    *,
    channel_id: int | None = None,
    thread_id: int | None = None,
    at: datetime,
    gap_seconds: int = DEFAULT_GAP_SECONDS,
) -> int:
    """Repair grouping around a single change (an insert, a delete, or an edit
    that added or removed an attachment) at time `at`. Returns rows changed.

    **Why a gap-wide window is provably enough**, rather than "everything
    after the change": a message further than `gap_seconds` from the change
    point cannot flip. It already starts a post on the gap rule alone, and
    gaining or losing a neighbour only ever moves its predecessor further
    away, which keeps that true. So only the messages within one gap either
    side can change answer -- and the window stays O(gap), not O(container),
    however deep in history the change lands.

    The extra second of slack on each side is because the gap rule is
    `> gap_seconds`: a message exactly that far out still merges, so it's
    inside the set that can flip.
    """
    slack = timedelta(seconds=gap_seconds + 1)
    return await regroup_range(
        conn,
        channel_id=channel_id,
        thread_id=thread_id,
        since=at - slack,
        until=at + slack,
        gap_seconds=gap_seconds,
    )


async def regroup_channel_and_threads(
    conn: psycopg.AsyncConnection,
    *,
    channel_id: int,
    gap_seconds: int = DEFAULT_GAP_SECONDS,
) -> int:
    """A channel and each of its threads are separate containers (messages
    carries exactly one of channel_id/thread_id), so "regroup this channel"
    means one call per container. Returns the total rows changed.
    """
    changed = await regroup_range(conn, channel_id=channel_id, gap_seconds=gap_seconds)
    async with conn.cursor() as cur:
        await cur.execute("SELECT id FROM threads WHERE parent_channel_id = %s", (channel_id,))
        thread_ids = [row["id"] for row in await cur.fetchall()]
    for tid in thread_ids:
        changed += await regroup_range(conn, thread_id=tid, gap_seconds=gap_seconds)
    return changed
