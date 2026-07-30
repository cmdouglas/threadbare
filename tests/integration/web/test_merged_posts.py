"""The web layer with consecutive-post merging actually switched on.

Everything else in tests/integration/web/ exercises the default-off path, and
its continued passing is itself the guarantee that an install which never opts
in sees no change. These are the other half.
"""

from datetime import UTC, datetime, timedelta

from threadbare.db import grouping

from .conftest import run
from .test_board import _seed_board, _seed_guild, _seed_message, _seed_user

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def _enable_merging(conn, *, gap_seconds=420):
    await conn.execute(
        """
        INSERT INTO site_settings (id, merge_consecutive_posts, merge_gap_seconds)
        VALUES (true, true, %s)
        ON CONFLICT (id) DO UPDATE SET
            merge_consecutive_posts = true, merge_gap_seconds = EXCLUDED.merge_gap_seconds
        """,
        (gap_seconds,),
    )


async def _seed_burst_channel(conn, *, channel_id=10, bursts=3, per_burst=3):
    """`bursts` runs of `per_burst` messages, alternating author so each run
    is exactly one merged post.
    """
    await _seed_guild(conn)
    await _seed_board(conn, channel_id=channel_id)
    await _seed_user(conn, user_id=100, display_name="alice")
    await _seed_user(conn, user_id=200, display_name="bob")
    message_id = 1000
    for burst in range(bursts):
        for offset in range(per_burst):
            await _seed_message(
                conn,
                message_id=message_id,
                channel_id=channel_id,
                author_id=100 if burst % 2 == 0 else 200,
                content=f"message {message_id}",
                posted_at=BASE + timedelta(minutes=burst * 60 + offset),
            )
            message_id += 1
    await grouping.regroup_range(conn, channel_id=channel_id)


def test_a_burst_renders_as_one_post_with_one_author_header(client, web_conn):
    run(_seed_burst_channel(web_conn, bursts=1))
    run(_enable_merging(web_conn))

    body = client.get("/board/10/continuous/page/1").data.decode()

    assert body.count('class="post"') == 1
    assert body.count('class="post-author"') == 1
    assert body.count('class="post-segment"') == 3
    # Every message is still there, and still individually addressable.
    for message_id in (1000, 1001, 1002):
        assert f'id="post-{message_id}"' in body
        assert f"message {message_id}" in body


def test_merging_off_renders_a_post_per_message_and_no_segments(client, web_conn):
    """The default path, asserted explicitly rather than only implied by the
    rest of the suite passing: no segment wrapper, and the article keeps its
    own anchor.
    """
    run(_seed_burst_channel(web_conn, bursts=1))

    body = client.get("/board/10/continuous/page/1").data.decode()

    assert body.count('class="post"') == 3
    assert "post-segment" not in body
    assert 'id="post-1000"' in body


def test_pagination_counts_posts_not_messages(client, web_conn):
    run(_seed_burst_channel(web_conn, bursts=3))
    run(_enable_merging(web_conn))

    body = client.get("/board/10/continuous/page/1?posts_per_page=10").data.decode()

    # Nine messages, three posts -- one page, not one page of nine.
    assert body.count('class="post"') == 3
    assert body.count('class="post-segment"') == 9


def test_a_page_boundary_never_splits_a_post(client, web_conn):
    # 12 posts of 2 messages each at 10 posts per page: page 1 holds posts
    # 1-10 whole, page 2 the remaining two. (posts_per_page only accepts
    # 10/25/50/100 -- anything else silently falls back to the default.)
    run(_seed_burst_channel(web_conn, bursts=12, per_burst=2))
    run(_enable_merging(web_conn))

    first = client.get("/board/10/continuous/page/1?posts_per_page=10").data.decode()
    second = client.get("/board/10/continuous/page/2?posts_per_page=10").data.decode()

    assert [m for m in range(1000, 1024) if f"message {m}" in first] == list(range(1000, 1020))
    assert [m for m in range(1000, 1024) if f"message {m}" in second] == list(range(1020, 1024))


def test_a_permalink_to_a_merged_in_message_lands_on_its_post_s_page(client, web_conn):
    """The group-head resolution trap, end to end. Message 1019 is the second
    segment of post 10, which sits on page 1 at ten posts per page. Counting
    heads before it *without* resolving to its head counts its own head too,
    giving 10 -- page 2, one page late.
    """
    run(_seed_burst_channel(web_conn, bursts=12, per_burst=2))
    run(_enable_merging(web_conn))

    # The reply-quote href is the permalink path that goes through the
    # resolution, so seed a reply pointing at a merged-in message.
    run(
        web_conn.execute(
            "INSERT INTO messages (id, channel_id, author_id, content, posted_at, reply_to_id) "
            "VALUES (2000, 10, 200, 'replying', %s, 1019)",
            (BASE + timedelta(hours=20),),
        )
    )
    run(grouping.regroup_range(web_conn, channel_id=10))

    body = client.get("/board/10/continuous/page/2?posts_per_page=10").data.decode()

    assert "/board/10/continuous/page/1#post-1019" in body


def test_jump_to_date_lands_on_the_page_holding_that_day_s_post(client, web_conn):
    run(_seed_burst_channel(web_conn, bursts=3))
    run(_enable_merging(web_conn))

    resp = client.get("/board/10/continuous/jump?date=2026-03-01&posts_per_page=2")

    assert resp.status_code == 302
    assert "/board/10/continuous/page/1" in resp.headers["Location"]


def test_a_reaction_filter_suppresses_merging(client, web_conn):
    """A post whose segments were selectively removed isn't a post, so a
    filtered view falls back to one post per message.
    """
    run(_seed_burst_channel(web_conn, bursts=1))
    run(_enable_merging(web_conn))
    run(
        web_conn.execute(
            "INSERT INTO reactions (message_id, emoji, count) "
            "VALUES (1000, '🔥', 1), (1002, '🔥', 1)"
        )
    )

    body = client.get("/board/10/continuous/page/1?reaction=%F0%9F%94%A5").data.decode()

    assert "post-segment" not in body
    assert body.count('class="post"') == 2


def test_the_gap_threshold_is_honoured_from_site_settings(client, web_conn):
    run(_seed_burst_channel(web_conn, bursts=1))
    run(_enable_merging(web_conn, gap_seconds=30))
    # Messages are a minute apart, so a 30-second gap merges nothing.
    run(grouping.regroup_range(web_conn, channel_id=10, gap_seconds=30))

    body = client.get("/board/10/continuous/page/1").data.decode()

    assert body.count('class="post"') == 3
    assert "post-segment" not in body
