"""regroup_range is the one place the "does this message start a new post"
predicate lives -- the migration backfill, the live ingestion path, the
nightly repair sweep, and the admin Regroup button are all this function with
different bounds. These tests are consequently the contract for all four.
"""

from datetime import UTC, datetime, timedelta

from threadbare.db import grouping

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def _seed_container(conn, *, channel_id=10, guild_id=1):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, 'Test Guild')", (guild_id,))
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, is_public) "
        "VALUES (%s, %s, 0, 'general', true)",
        (channel_id, guild_id),
    )
    for user_id, name in ((100, "alice"), (200, "bob")):
        await conn.execute(
            "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, name),
        )


async def _seed_message(
    conn,
    *,
    message_id,
    author_id=100,
    minutes=0,
    channel_id=10,
    thread_id=None,
    reply_to_id=None,
    type=0,
    attachment=False,
):
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, thread_id, author_id, content,
                              reply_to_id, posted_at, type)
        VALUES (%s, %s, %s, %s, 'hi', %s, %s, %s)
        """,
        (
            message_id,
            channel_id,
            thread_id,
            author_id,
            reply_to_id,
            BASE + timedelta(minutes=minutes),
            type,
        ),
    )
    if attachment:
        await conn.execute(
            """
            INSERT INTO attachments (id, message_id, filename, size, cached_url, url_expires_at)
            VALUES (%s, %s, 'cat.png', 1, 'https://cdn.example/cat.png', now())
            """,
            (message_id, message_id),
        )


async def _flags(conn, *, channel_id=10, thread_id=None) -> dict[int, bool]:
    column, value = ("thread_id", thread_id) if thread_id else ("channel_id", channel_id)
    async with conn.cursor() as cur:
        await cur.execute(
            f"SELECT id, starts_group FROM messages WHERE {column} = %s ORDER BY posted_at, id",
            (value,),
        )
        return {row["id"]: row["starts_group"] for row in await cur.fetchall()}


async def test_a_run_by_one_author_within_the_gap_becomes_a_single_post(db_conn):
    await _seed_container(db_conn)
    for i, minutes in enumerate((0, 1, 3)):
        await _seed_message(db_conn, message_id=1000 + i, minutes=minutes)

    await grouping.regroup_range(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1000: True, 1001: False, 1002: False}


async def test_a_different_author_starts_a_new_post(db_conn):
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, author_id=100, minutes=0)
    await _seed_message(db_conn, message_id=1001, author_id=200, minutes=1)
    await _seed_message(db_conn, message_id=1002, author_id=100, minutes=2)

    await grouping.regroup_range(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1000: True, 1001: True, 1002: True}


async def test_a_gap_longer_than_the_threshold_starts_a_new_post(db_conn):
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=1001, minutes=6)  # within 7min
    await _seed_message(db_conn, message_id=1002, minutes=20)  # well past it

    await grouping.regroup_range(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1000: True, 1001: False, 1002: True}


async def test_the_gap_threshold_is_configurable(db_conn):
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=1001, minutes=6)

    await grouping.regroup_range(db_conn, channel_id=10, gap_seconds=60)

    assert await _flags(db_conn) == {1000: True, 1001: True}


async def test_a_reply_starts_a_new_post(db_conn):
    """A reply answers something specific and carries its own quote block;
    swallowing it into the previous post would lose that context.
    """
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=1001, minutes=1, reply_to_id=1000)

    await grouping.regroup_range(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1000: True, 1001: True}


async def test_a_system_message_neither_joins_nor_is_joined(db_conn):
    """Joins/boosts/pin notices render through a separate path entirely, so
    they break a run in both directions rather than just their own.
    """
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=1001, minutes=1, type=7)
    await _seed_message(db_conn, message_id=1002, minutes=2)

    await grouping.regroup_range(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1000: True, 1001: True, 1002: True}


async def test_a_message_with_attachments_stands_alone_in_both_directions(db_conn):
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=1001, minutes=1, attachment=True)
    await _seed_message(db_conn, message_id=1002, minutes=2)

    await grouping.regroup_range(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1000: True, 1001: True, 1002: True}


async def test_a_range_regroup_reads_the_message_before_its_window(db_conn):
    """The single most likely bug in this feature: scoping the window to
    [since, until) alone means the first message in it sees no predecessor and
    is wrongly marked a head, so every regroup boundary sprouts a spurious
    post split.
    """
    await _seed_container(db_conn)
    for i, minutes in enumerate((0, 1, 2)):
        await _seed_message(db_conn, message_id=1000 + i, minutes=minutes)
    await grouping.regroup_range(db_conn, channel_id=10)

    # Re-run scoped to the tail only. 1001's head (1000) sits outside the
    # window, but the answer for 1001 must not change.
    await grouping.regroup_range(db_conn, channel_id=10, since=BASE + timedelta(minutes=1))

    assert await _flags(db_conn) == {1000: True, 1001: False, 1002: False}


async def test_a_range_regroup_leaves_rows_outside_its_window_alone(db_conn):
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=1001, minutes=1)
    await db_conn.execute("UPDATE messages SET starts_group = true WHERE id = 1001")

    # A window that starts after both messages touches neither.
    await grouping.regroup_range(db_conn, channel_id=10, since=BASE + timedelta(minutes=30))

    assert await _flags(db_conn) == {1000: True, 1001: True}


async def test_only_rows_whose_flag_actually_changes_are_written(db_conn):
    """The IS DISTINCT FROM guard is what keeps a gap-threshold tweak from
    rewriting every row of a million-message channel.
    """
    await _seed_container(db_conn)
    for i, minutes in enumerate((0, 1, 2)):
        await _seed_message(db_conn, message_id=1000 + i, minutes=minutes)

    first = await grouping.regroup_range(db_conn, channel_id=10)
    second = await grouping.regroup_range(db_conn, channel_id=10)

    assert first == 2  # 1001 and 1002 flip to false; 1000 was already true
    assert second == 0


async def test_threads_are_grouped_independently_of_their_parent_channel(db_conn):
    await _seed_container(db_conn)
    await db_conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) "
        "VALUES (50, 10, 'a thread', now())"
    )
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=2000, minutes=1, channel_id=None, thread_id=50)
    await _seed_message(db_conn, message_id=2001, minutes=2, channel_id=None, thread_id=50)

    await grouping.regroup_range(db_conn, thread_id=50)

    assert await _flags(db_conn, thread_id=50) == {2000: True, 2001: False}
    # The channel's own message is untouched by a thread-scoped regroup.
    assert await _flags(db_conn) == {1000: True}


async def test_deleting_a_separator_lets_the_messages_around_it_merge(db_conn):
    """Deletes merge posts as well as split them -- the case a design that
    only recomputed downward from the deleted row would miss.
    """
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, author_id=100, minutes=0)
    await _seed_message(db_conn, message_id=1001, author_id=200, minutes=1)
    await _seed_message(db_conn, message_id=1002, author_id=100, minutes=2)
    await grouping.regroup_range(db_conn, channel_id=10)
    assert await _flags(db_conn) == {1000: True, 1001: True, 1002: True}

    await db_conn.execute("DELETE FROM messages WHERE id = 1001")
    await grouping.regroup_range(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1000: True, 1002: False}


async def test_deleting_a_head_promotes_its_successor(db_conn):
    await _seed_container(db_conn)
    for i, minutes in enumerate((0, 1, 2)):
        await _seed_message(db_conn, message_id=1000 + i, minutes=minutes)
    await grouping.regroup_range(db_conn, channel_id=10)

    await db_conn.execute("DELETE FROM messages WHERE id = 1000")
    await grouping.regroup_range(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1001: True, 1002: False}


async def test_regroup_around_fixes_the_neighbourhood_of_a_change(db_conn):
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, author_id=100, minutes=0)
    await _seed_message(db_conn, message_id=1001, author_id=200, minutes=1)
    await _seed_message(db_conn, message_id=1002, author_id=100, minutes=2)
    await grouping.regroup_range(db_conn, channel_id=10)

    await db_conn.execute("DELETE FROM messages WHERE id = 1001")
    await grouping.regroup_around(db_conn, channel_id=10, at=BASE + timedelta(minutes=1))

    assert await _flags(db_conn) == {1000: True, 1002: False}


async def test_regroup_around_leaves_messages_beyond_the_gap_alone(db_conn):
    """The window is bounded by the merge gap in both directions, and that's
    only safe because a message further than the gap from the change point
    can't flip: it already started a post on the gap rule, and losing a
    neighbour only moves its predecessor further away.
    """
    await _seed_container(db_conn)
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=1001, minutes=1)
    # Hours later, and deliberately left with a wrong flag: a correct
    # neighbourhood regroup must not reach far enough to "fix" it.
    await _seed_message(db_conn, message_id=1002, minutes=600)
    await db_conn.execute("UPDATE messages SET starts_group = false WHERE id = 1002")

    await grouping.regroup_around(db_conn, channel_id=10, at=BASE)

    assert await _flags(db_conn) == {1000: True, 1001: False, 1002: False}


async def test_regroup_channel_and_threads_covers_every_container(db_conn):
    await _seed_container(db_conn)
    await db_conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) "
        "VALUES (50, 10, 'a thread', now())"
    )
    await _seed_message(db_conn, message_id=1000, minutes=0)
    await _seed_message(db_conn, message_id=1001, minutes=1)
    await _seed_message(db_conn, message_id=2000, minutes=0, channel_id=None, thread_id=50)
    await _seed_message(db_conn, message_id=2001, minutes=1, channel_id=None, thread_id=50)

    await grouping.regroup_channel_and_threads(db_conn, channel_id=10)

    assert await _flags(db_conn) == {1000: True, 1001: False}
    assert await _flags(db_conn, thread_id=50) == {2000: True, 2001: False}
