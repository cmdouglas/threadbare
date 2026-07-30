"""The read half of consecutive-post merging: pagination that counts posts
rather than messages, and the message-to-page resolution every permalink,
search result, and jump link depends on.

The resolution trap these exist for: counting *heads* strictly before a
merged-in message includes that message's own head, which lands a page late
at every boundary. Every caller has to resolve to the group head first.
"""

from datetime import UTC, datetime, timedelta

from threadbare.db import grouping, queries

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def _seed(conn, *, channel_id=10, guild_id=1):
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


async def _seed_message(conn, *, message_id, author_id=100, minutes=0, channel_id=10):
    await conn.execute(
        "INSERT INTO messages (id, channel_id, author_id, content, posted_at) "
        "VALUES (%s, %s, %s, 'hi', %s)",
        (message_id, channel_id, author_id, BASE + timedelta(minutes=minutes)),
    )


async def _seed_bursts(conn, *, bursts, per_burst=3):
    """`bursts` runs of `per_burst` messages each, alternating author so every
    run is one merged post. Ids are 1000 + sequential, timestamps one minute
    apart within a run and an hour apart between runs.
    """
    message_id = 1000
    for burst in range(bursts):
        for offset in range(per_burst):
            await _seed_message(
                conn,
                message_id=message_id,
                author_id=100 if burst % 2 == 0 else 200,
                minutes=burst * 60 + offset,
            )
            message_id += 1
    await grouping.regroup_range(conn, channel_id=10)


async def test_counting_unmerged_still_counts_every_message(db_conn):
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=2)

    assert await queries.count_messages_before(db_conn, channel_id=10) == 6


async def test_counting_merged_counts_posts_not_messages(db_conn):
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=2)

    assert await queries.count_messages_before(db_conn, channel_id=10, merged=True) == 2


async def test_a_merged_page_returns_whole_posts(db_conn):
    """The point of grouping at ingestion rather than at render time: a page
    boundary lands between posts, never inside one.
    """
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=3)

    rows = await queries.get_messages_page(
        db_conn, channel_id=10, page=1, page_size=2, total=3, merged=True
    )

    # Two posts, whole: six messages, not the first two.
    assert [row["id"] for row in rows] == [1000, 1001, 1002, 1003, 1004, 1005]


async def test_a_merged_page_walking_backward_returns_whole_posts(db_conn):
    """get_messages_page walks from whichever end of the container is closer,
    so the last page takes a different code path from the first and has to be
    exercised on its own.
    """
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=4)

    rows = await queries.get_messages_page(
        db_conn, channel_id=10, page=2, page_size=2, total=4, merged=True
    )

    assert [row["id"] for row in rows] == [1006, 1007, 1008, 1009, 1010, 1011]


async def test_the_last_merged_page_is_not_truncated(db_conn):
    """The limit+1 boundary fetch has no extra head to stop at on the final
    page; getting that wrong drops the last post's trailing messages.
    """
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=3)

    rows = await queries.get_messages_page(
        db_conn, channel_id=10, page=2, page_size=2, total=3, merged=True
    )

    assert [row["id"] for row in rows] == [1006, 1007, 1008]


async def test_a_merged_page_beyond_the_end_is_empty(db_conn):
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=1)

    rows = await queries.get_messages_page(
        db_conn, channel_id=10, page=5, page_size=2, total=1, merged=True
    )

    assert rows == []


async def test_group_head_of_a_head_is_itself(db_conn):
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=1)

    head = await queries.get_group_head_for_message(db_conn, message_id=1000)

    assert head["id"] == 1000


async def test_group_head_of_a_merged_in_message_is_its_head(db_conn):
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=1)

    head = await queries.get_group_head_for_message(db_conn, message_id=1002)

    assert head["id"] == 1000


async def test_group_head_of_an_unknown_message_is_none(db_conn):
    await _seed(db_conn)

    assert await queries.get_group_head_for_message(db_conn, message_id=999) is None


async def test_a_merged_in_message_resolves_to_its_head_s_page_not_the_next_one(db_conn):
    """The whole reason get_group_head_for_message exists. Counting heads
    strictly before message 1005 gives 2 (its own head included), which is
    page 2 at a page size of 2 -- but 1005 is the last message of the *second*
    post, which sits on page 1.
    """
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=3)

    head = await queries.get_group_head_for_message(db_conn, message_id=1005)
    preceding = await queries.count_messages_before(
        db_conn, channel_id=10, before=(head["posted_at"], head["id"]), merged=True
    )

    assert preceding == 1  # one whole post ahead of it

    naive = await queries.count_messages_before(
        db_conn,
        channel_id=10,
        before=(BASE + timedelta(minutes=62), 1005),
        merged=True,
    )
    assert naive == 2  # what the same call gives without resolving to the head


async def test_merged_counting_still_honours_a_date_window(db_conn):
    await _seed(db_conn)
    await _seed_bursts(db_conn, bursts=3)

    total = await queries.count_messages_before(
        db_conn, channel_id=10, since=BASE + timedelta(minutes=30), merged=True
    )

    assert total == 2  # the second and third bursts


async def test_threads_paginate_merged_independently(db_conn):
    await _seed(db_conn)
    await db_conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) "
        "VALUES (50, 10, 'a thread', now())"
    )
    for offset, message_id in enumerate((2000, 2001)):
        await db_conn.execute(
            "INSERT INTO messages (id, thread_id, author_id, content, posted_at) "
            "VALUES (%s, 50, 100, 'hi', %s)",
            (message_id, BASE + timedelta(minutes=offset)),
        )
    await grouping.regroup_range(db_conn, thread_id=50)

    assert await queries.count_messages_before(db_conn, thread_id=50, merged=True) == 1
    rows = await queries.get_messages_page(
        db_conn, thread_id=50, page=1, page_size=25, total=1, merged=True
    )
    assert [row["id"] for row in rows] == [2000, 2001]
