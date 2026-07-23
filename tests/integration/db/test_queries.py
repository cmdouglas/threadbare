from datetime import UTC, datetime, timedelta

from threadbare.db import queries


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10, is_public=True):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, 0, 'general', %s)
        """,
        (channel_id, guild_id, is_public),
    )


async def _seed_guild(conn, *, guild_id):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (guild_id, "Test Guild"),
    )


async def _seed_user(conn, *, user_id, display_name, avatar_hash=None, is_bot=False, role_ids=None):
    await conn.execute(
        "INSERT INTO users (id, display_name, avatar_hash, is_bot, role_ids) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (user_id, display_name, avatar_hash, is_bot, role_ids or []),
    )


async def _seed_role(
    conn, *, role_id, guild_id=1, name="a role", color=0, position=0, permissions=0
):
    await conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (role_id, guild_id, name, color, position, permissions),
    )


async def _seed_channel(conn, *, channel_id, guild_id=1, type=0, parent_id=None):
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, parent_id) "
        "VALUES (%s, %s, %s, 'a channel', %s)",
        (channel_id, guild_id, type, parent_id),
    )


async def _seed_channel_role_overwrite(conn, *, channel_id, role_id, allow=0, deny=0):
    await conn.execute(
        "INSERT INTO channel_role_overwrites (channel_id, role_id, allow, deny) "
        "VALUES (%s, %s, %s, %s)",
        (channel_id, role_id, allow, deny),
    )


async def _seed_channel_member_overwrite(conn, *, channel_id, user_id, allow=0, deny=0):
    await conn.execute(
        "INSERT INTO channel_member_overwrites (channel_id, user_id, allow, deny) "
        "VALUES (%s, %s, %s, %s)",
        (channel_id, user_id, allow, deny),
    )


async def _seed_message(
    conn, *, message_id, channel_id, author_id, content="hello", posted_at=None
):
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, COALESCE(%s, now()))
        """,
        (message_id, channel_id, author_id, content, posted_at),
    )


async def _seed_thread(conn, *, thread_id, parent_channel_id, name="a thread"):
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, now())",
        (thread_id, parent_channel_id, name),
    )


async def _seed_thread_message(
    conn, *, message_id, thread_id, author_id, content="hello", posted_at=None
):
    await conn.execute(
        """
        INSERT INTO messages (id, thread_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, COALESCE(%s, now()))
        """,
        (message_id, thread_id, author_id, content, posted_at),
    )


async def test_get_message_for_render_returns_message_with_author_display_name(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice", avatar_hash="abcdef")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100, content="hi there")

    row = await queries.get_message_for_render(db_conn, 1000)

    assert row["id"] == 1000
    assert row["content"] == "hi there"
    assert row["author_id"] == 100
    assert row["author_display_name"] == "alice"
    assert row["author_avatar_hash"] == "abcdef"
    assert row["channel_id"] == 10
    assert row["thread_id"] is None
    assert row["reply_to_id"] is None


async def test_get_message_for_render_returns_none_for_unknown_message(db_conn):
    assert await queries.get_message_for_render(db_conn, 999999) is None


async def test_get_message_for_render_returns_author_is_bot(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="a-bot", is_bot=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)

    row = await queries.get_message_for_render(db_conn, 1000)

    assert row["author_is_bot"] is True


async def test_get_message_for_render_returns_null_role_color_when_no_colored_role(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice", role_ids=[])
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)

    row = await queries.get_message_for_render(db_conn, 1000)

    assert row["author_role_color"] is None


async def test_get_message_for_render_picks_highest_position_colored_role(db_conn):
    # Discord's own algorithm: the highest-position role among those with a
    # non-zero color wins -- a higher-position but uncolored role must not
    # shadow a lower-position colored one.
    await _seed_guild_and_channel(db_conn)
    await _seed_role(db_conn, role_id=1, name="Uncolored (higher)", color=0, position=5)
    await _seed_role(db_conn, role_id=2, name="Blue (lower)", color=0x0000FF, position=3)
    await _seed_role(db_conn, role_id=3, name="Red (lowest)", color=0xFF0000, position=1)
    await _seed_user(db_conn, user_id=100, display_name="alice", role_ids=[1, 2, 3])
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)

    row = await queries.get_message_for_render(db_conn, 1000)

    assert row["author_role_color"] == 0x0000FF


async def test_get_attachments_for_message_returns_rows_in_id_order(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)
    expires_at = datetime(2026, 1, 2, tzinfo=UTC)
    for attachment_id, filename in [(2001, "b.png"), (2000, "a.png")]:
        await db_conn.execute(
            """
            INSERT INTO attachments (
                id, message_id, filename, content_type, size, cached_url, url_expires_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (attachment_id, 1000, filename, "image/png", 100, "https://example.com/x", expires_at),
        )

    rows = await queries.get_attachments_for_message(db_conn, 1000)

    assert [row["id"] for row in rows] == [2000, 2001]
    assert rows[0]["filename"] == "a.png"


async def test_get_attachments_for_message_returns_empty_list_for_no_attachments(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)

    assert await queries.get_attachments_for_message(db_conn, 1000) == []


async def test_get_embeds_for_message_returns_rows_ordered_by_position(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)
    await db_conn.execute(
        "INSERT INTO embeds (message_id, position, title) VALUES (%s, %s, %s)", (1000, 1, "second")
    )
    await db_conn.execute(
        "INSERT INTO embeds (message_id, position, title) VALUES (%s, %s, %s)", (1000, 0, "first")
    )

    rows = await queries.get_embeds_for_message(db_conn, 1000)

    assert [row["title"] for row in rows] == ["first", "second"]


async def test_get_embeds_for_message_includes_video_url(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)
    await db_conn.execute(
        "INSERT INTO embeds (message_id, position, video_url) VALUES (%s, %s, %s)",
        (1000, 0, "https://example.com/clip.mp4"),
    )

    rows = await queries.get_embeds_for_message(db_conn, 1000)

    assert rows[0]["video_url"] == "https://example.com/clip.mp4"


async def test_get_reactions_for_message_returns_emoji_count_pairs(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)
    await db_conn.execute(
        "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (1000, "👍", 3)
    )

    assert await queries.get_reactions_for_message(db_conn, 1000) == [("👍", 3)]


async def test_resolve_users_returns_display_names_for_known_ids(db_conn):
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_user(db_conn, user_id=101, display_name="bob")

    result = await queries.resolve_users(db_conn, [100, 101, 999])

    assert result == {100: "alice", 101: "bob"}


async def test_resolve_users_returns_empty_dict_for_no_ids(db_conn):
    assert await queries.resolve_users(db_conn, []) == {}


async def test_resolve_channels_returns_names_for_known_ids(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, %s)",
        (11, 1, "off-topic"),
    )

    result = await queries.resolve_channels(db_conn, [10, 11, 999])

    assert result == {10: "general", 11: "off-topic"}


async def test_resolve_channels_returns_empty_dict_for_no_ids(db_conn):
    assert await queries.resolve_channels(db_conn, []) == {}


T1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
T3 = datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC)


async def test_count_messages_before_with_no_before_returns_total_for_channel(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T2)

    assert await queries.count_messages_before(db_conn, channel_id=10) == 2


async def test_count_messages_before_with_no_before_returns_total_for_thread(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_thread_message(db_conn, message_id=1, thread_id=3000, author_id=100, posted_at=T1)

    assert await queries.count_messages_before(db_conn, thread_id=3000) == 1


async def test_count_messages_before_a_specific_message_counts_only_earlier_ones(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T2)
    await _seed_message(db_conn, message_id=3, channel_id=10, author_id=100, posted_at=T3)

    n = await queries.count_messages_before(db_conn, channel_id=10, before=(T3, 3))

    assert n == 2


async def test_count_messages_before_breaks_ties_on_id_at_equal_posted_at(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T1)

    assert await queries.count_messages_before(db_conn, channel_id=10, before=(T1, 2)) == 1
    assert await queries.count_messages_before(db_conn, channel_id=10, before=(T1, 1)) == 0


async def test_count_messages_before_a_date_counts_all_earlier_messages(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T3)

    assert await queries.count_messages_before(db_conn, channel_id=10, before=T2) == 1


async def test_count_messages_before_windowed_by_since_and_until(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T2)
    await _seed_message(db_conn, message_id=3, channel_id=10, author_id=100, posted_at=T3)

    n = await queries.count_messages_before(db_conn, channel_id=10, since=T2, until=T3)

    assert n == 1


async def test_get_board_post_aggregates_combines_direct_and_thread_messages(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_user(db_conn, user_id=101, display_name="bob")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_thread_message(db_conn, message_id=2, thread_id=3000, author_id=101, posted_at=T2)

    result = await queries.get_board_post_aggregates(db_conn, [10])

    assert result[10]["post_count"] == 2
    assert result[10]["last_message_id"] == 2
    assert result[10]["last_posted_at"] == T2
    assert result[10]["last_author_id"] == 101


async def test_get_board_post_aggregates_left_fills_boards_with_no_messages(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)

    result = await queries.get_board_post_aggregates(db_conn, [10])

    assert result == {
        10: {
            "post_count": 0,
            "last_message_id": None,
            "last_posted_at": None,
            "last_author_id": None,
        }
    }


async def test_get_board_post_aggregates_returns_empty_dict_for_no_ids(db_conn):
    assert await queries.get_board_post_aggregates(db_conn, []) == {}


async def test_get_thread_post_aggregates_left_fills_and_computes_last_post(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_thread(db_conn, thread_id=3001, parent_channel_id=10)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_thread_message(db_conn, message_id=1, thread_id=3000, author_id=100, posted_at=T1)
    await _seed_thread_message(db_conn, message_id=2, thread_id=3000, author_id=100, posted_at=T2)

    result = await queries.get_thread_post_aggregates(db_conn, [3000, 3001])

    assert result[3000]["post_count"] == 2
    assert result[3000]["last_message_id"] == 2
    assert result[3001]["post_count"] == 0
    assert result[3001]["last_message_id"] is None


async def test_count_topics_for_board(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_thread(db_conn, thread_id=3001, parent_channel_id=10)

    assert await queries.count_topics_for_board(db_conn, 10) == 2


async def test_get_messages_page_matches_get_message_for_render_column_shape(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)

    [page_row] = await queries.get_messages_page(db_conn, channel_id=10, page=1, page_size=25)
    render_row = await queries.get_message_for_render(db_conn, 1)

    assert page_row.keys() == render_row.keys()
    assert page_row == render_row


async def test_get_messages_page_orders_by_posted_at_then_id(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T2)
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)

    rows = await queries.get_messages_page(db_conn, channel_id=10, page=1, page_size=25)

    assert [r["id"] for r in rows] == [1, 2]


async def test_get_messages_page_paginates_with_page_size(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    for i, t in enumerate([T1, T2, T3], start=1):
        await _seed_message(db_conn, message_id=i, channel_id=10, author_id=100, posted_at=t)

    page1 = await queries.get_messages_page(db_conn, channel_id=10, page=1, page_size=2)
    page2 = await queries.get_messages_page(db_conn, channel_id=10, page=2, page_size=2)

    assert [r["id"] for r in page1] == [1, 2]
    assert [r["id"] for r in page2] == [3]


async def test_get_messages_page_windowed_by_since_and_until(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T2)
    await _seed_message(db_conn, message_id=3, channel_id=10, author_id=100, posted_at=T3)

    rows = await queries.get_messages_page(
        db_conn, channel_id=10, page=1, page_size=25, since=T2, until=T3
    )

    assert [r["id"] for r in rows] == [2]


async def test_get_messages_page_for_a_thread(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_thread_message(db_conn, message_id=1, thread_id=3000, author_id=100, posted_at=T1)

    rows = await queries.get_messages_page(db_conn, thread_id=3000, page=1, page_size=25)

    assert [r["id"] for r in rows] == [1]


async def test_get_attachment_by_id_returns_row(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)
    expires_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_conn.execute(
        """
        INSERT INTO attachments (
            id, message_id, filename, content_type, size, cached_url, url_expires_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (2000, 1000, "cat.png", "image/png", 100, "https://example.com/cat.png", expires_at),
    )

    row = await queries.get_attachment_by_id(db_conn, 2000)

    assert row["filename"] == "cat.png"
    assert row["cached_url"] == "https://example.com/cat.png"


async def test_get_attachment_by_id_returns_none_for_unknown_id(db_conn):
    assert await queries.get_attachment_by_id(db_conn, 999999) is None


async def test_update_attachment_cache_updates_url_and_expiry(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100)
    old_expires_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_conn.execute(
        """
        INSERT INTO attachments (
            id, message_id, filename, content_type, size, cached_url, url_expires_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (2000, 1000, "cat.png", "image/png", 100, "https://example.com/old.png", old_expires_at),
    )
    new_expires_at = datetime(2026, 1, 3, tzinfo=UTC)

    await queries.update_attachment_cache(
        db_conn, 2000, cached_url="https://example.com/new.png", url_expires_at=new_expires_at
    )

    row = await queries.get_attachment_by_id(db_conn, 2000)
    assert row["cached_url"] == "https://example.com/new.png"
    assert row["url_expires_at"] == new_expires_at


async def test_search_messages_matches_full_text(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(
        db_conn, message_id=1, channel_id=10, author_id=100, content="a message about pizza"
    )
    await _seed_message(
        db_conn, message_id=2, channel_id=10, author_id=100, content="unrelated content"
    )

    rows = await queries.search_messages(db_conn, query="pizza")

    assert [r["id"] for r in rows] == [1]
    assert "snippet" in rows[0]


async def test_search_messages_filters_by_author(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_user(db_conn, user_id=101, display_name="bob")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, content="pizza time")
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=101, content="pizza too")

    rows = await queries.search_messages(db_conn, query="pizza", author="ali")

    assert [r["id"] for r in rows] == [1]


async def test_search_messages_filters_by_channel_including_child_threads(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await _seed_guild_and_channel(db_conn, guild_id=2, channel_id=11)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, content="pizza a")
    await _seed_thread_message(
        db_conn, message_id=2, thread_id=3000, author_id=100, content="pizza b"
    )
    await _seed_message(db_conn, message_id=3, channel_id=11, author_id=100, content="pizza c")

    rows = await queries.search_messages(db_conn, query="pizza", channel_id=10)

    assert {r["id"] for r in rows} == {1, 2}


async def test_search_messages_filters_by_date_range(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(
        db_conn, message_id=1, channel_id=10, author_id=100, content="pizza old", posted_at=T1
    )
    await _seed_message(
        db_conn, message_id=2, channel_id=10, author_id=100, content="pizza new", posted_at=T3
    )

    rows = await queries.search_messages(db_conn, query="pizza", after=T2)

    assert [r["id"] for r in rows] == [2]


async def test_search_messages_excludes_non_indexed_channels(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await db_conn.execute("UPDATE channels SET indexed = false WHERE id = 10")
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, content="pizza")

    assert await queries.search_messages(db_conn, query="pizza") == []


async def test_search_messages_preceding_count_reflects_position_in_container(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T2)
    await _seed_message(
        db_conn, message_id=3, channel_id=10, author_id=100, content="pizza", posted_at=T3
    )

    [row] = await queries.search_messages(db_conn, query="pizza")

    assert row["preceding_count"] == 2


async def test_search_messages_handles_malformed_query_without_raising(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, content="pizza")

    assert await queries.search_messages(db_conn, query='"unterminated quote') == []


async def test_count_search_results_matches_search_messages_count(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, content="pizza a")
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, content="pizza b")

    assert await queries.count_search_results(db_conn, query="pizza") == 2


async def test_get_user_returns_row(db_conn):
    await _seed_user(db_conn, user_id=100, display_name="alice")

    row = await queries.get_user(db_conn, 100)

    assert row["display_name"] == "alice"


async def test_get_user_returns_none_for_unknown_id(db_conn):
    assert await queries.get_user(db_conn, 999999) is None


async def test_get_user_returns_is_bot_and_role_ids(db_conn):
    await _seed_user(db_conn, user_id=100, display_name="a-bot", is_bot=True, role_ids=[1, 2])

    row = await queries.get_user(db_conn, 100)

    assert row["is_bot"] is True
    assert row["role_ids"] == [1, 2]


async def test_get_roles_by_ids_returns_rows_ordered_by_position_descending(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_role(db_conn, role_id=1, name="Low", color=0x0000FF, position=1)
    await _seed_role(db_conn, role_id=2, name="High", color=0xFF0000, position=5)

    rows = await queries.get_roles_by_ids(db_conn, [1, 2])

    assert [r["name"] for r in rows] == ["High", "Low"]


async def test_get_roles_by_ids_returns_empty_for_empty_input(db_conn):
    assert await queries.get_roles_by_ids(db_conn, []) == []


async def test_get_guild_returns_row(db_conn):
    # guild_id=1 collides with tests/e2e's fixed E2E_GUILD_ID (a real commit
    # against the same test database, not rolled back by this fixture) --
    # use a distinct id so this test doesn't depend on e2e run/cleanup order.
    await _seed_guild(db_conn, guild_id=4242)

    row = await queries.get_guild(db_conn, 4242)

    assert row["id"] == 4242
    assert row["name"] == "Test Guild"


async def test_get_guild_returns_none_for_unknown_id(db_conn):
    assert await queries.get_guild(db_conn, 999999) is None


async def test_get_post_count_for_user_counts_only_indexed_channels(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await _seed_guild_and_channel(db_conn, guild_id=2, channel_id=11)
    await db_conn.execute("UPDATE channels SET indexed = false WHERE id = 11")
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100)
    await _seed_message(db_conn, message_id=2, channel_id=11, author_id=100)

    assert await queries.get_post_count_for_user(db_conn, 100) == 1


async def test_get_threads_for_board_orders_newest_first(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await db_conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, %s)",
        (3000, 10, "older", T1),
    )
    await db_conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, %s)",
        (3001, 10, "newer", T2),
    )

    rows = await queries.get_threads_for_board(db_conn, 10, page=1, page_size=25)

    assert [r["name"] for r in rows] == ["newer", "older"]


async def test_get_threads_for_board_paginates(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    for i in range(3):
        await db_conn.execute(
            "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, %s)",
            (3000 + i, 10, f"thread {i}", T1 + timedelta(seconds=i)),
        )

    page1 = await queries.get_threads_for_board(db_conn, 10, page=1, page_size=2)
    page2 = await queries.get_threads_for_board(db_conn, 10, page=2, page_size=2)

    assert len(page1) == 2
    assert len(page2) == 1


async def test_get_weeks_for_board_groups_by_iso_week(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    monday_week_28 = datetime(2026, 7, 6, tzinfo=UTC)
    await _seed_message(
        db_conn, message_id=1, channel_id=10, author_id=100, posted_at=monday_week_28
    )
    await _seed_message(
        db_conn,
        message_id=2,
        channel_id=10,
        author_id=100,
        posted_at=monday_week_28 + timedelta(days=1),
    )
    next_week = monday_week_28 + timedelta(days=7)
    await _seed_message(db_conn, message_id=3, channel_id=10, author_id=100, posted_at=next_week)

    weeks = await queries.get_weeks_for_board(db_conn, 10)

    assert [(w["week_id"], w["post_count"]) for w in weeks] == [("2026-W29", 1), ("2026-W28", 2)]


async def test_get_channel_returns_row(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)

    row = await queries.get_channel(db_conn, 10)

    assert row["id"] == 10
    assert row["name"] == "general"
    assert row["type"] == 0


async def test_get_channel_returns_none_for_unknown_id(db_conn):
    assert await queries.get_channel(db_conn, 999999) is None


async def test_get_thread_returns_row(db_conn):
    await _seed_guild_and_channel(db_conn, channel_id=10)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10, name="a thread")

    row = await queries.get_thread(db_conn, 3000)

    assert row["id"] == 3000
    assert row["parent_channel_id"] == 10
    assert row["name"] == "a thread"


async def test_get_thread_returns_none_for_unknown_id(db_conn):
    assert await queries.get_thread(db_conn, 999999) is None


async def test_get_boards_and_categories_includes_all_categories(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, is_public) VALUES (%s, %s, 4, %s, false)",
        (1, 1, "a category"),
    )

    rows = await queries.get_boards_and_categories(db_conn, 1)

    assert [r["id"] for r in rows] == [1]


async def test_get_boards_and_categories_includes_topic(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await db_conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed, topic)
        VALUES (10, 1, 0, 'general', true, true, 'a channel topic')
        """
    )

    rows = await queries.get_boards_and_categories(db_conn, 1)

    assert rows[0]["topic"] == "a channel topic"


async def test_get_boards_and_categories_excludes_non_public_or_non_indexed_boards(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await db_conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (10, 1, 0, 'public', true, true),
               (11, 1, 0, 'private', false, true),
               (12, 1, 0, 'unindexed', true, false)
        """
    )

    rows = await queries.get_boards_and_categories(db_conn, 1)

    assert [r["id"] for r in rows] == [10]


async def test_get_boards_and_categories_excludes_voice_and_stage_voice_channels(db_conn):
    # Defense-in-depth against a stale row from before voice/stage channels
    # were excluded at discovery time -- even public+indexed, it must not
    # show up as a browsable board.
    await _seed_guild(db_conn, guild_id=1)
    await db_conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (10, 1, 0, 'public', true, true),
               (20, 1, 2, 'a voice channel', true, true),
               (21, 1, 13, 'a stage', true, true)
        """
    )

    rows = await queries.get_boards_and_categories(db_conn, 1)

    assert [r["id"] for r in rows] == [10]


async def test_get_boards_and_categories_only_for_the_given_guild(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_guild(db_conn, guild_id=2)
    await db_conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (10, 1, 0, 'a', true, true), (11, 2, 0, 'b', true, true)
        """
    )

    rows = await queries.get_boards_and_categories(db_conn, 1)

    assert [r["id"] for r in rows] == [10]


async def test_get_recent_posts_for_user_orders_newest_first_and_respects_limit(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1, channel_id=10, author_id=100, posted_at=T1)
    await _seed_message(db_conn, message_id=2, channel_id=10, author_id=100, posted_at=T2)
    await _seed_message(db_conn, message_id=3, channel_id=10, author_id=100, posted_at=T3)

    rows = await queries.get_recent_posts_for_user(db_conn, 100, limit=2)

    assert [r["id"] for r in rows] == [3, 2]


async def test_get_base_permissions_ors_everyone_and_held_roles(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_role(db_conn, role_id=1, guild_id=1, permissions=1 << 10)
    await _seed_role(db_conn, role_id=42, guild_id=1, permissions=1 << 16)

    result = await queries.get_base_permissions(db_conn, guild_id=1, role_ids=[42])

    assert result == ((1 << 10) | (1 << 16))


async def test_get_base_permissions_with_empty_role_ids_still_includes_everyone(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_role(db_conn, role_id=1, guild_id=1, permissions=1 << 10)

    result = await queries.get_base_permissions(db_conn, guild_id=1, role_ids=[])

    assert result == (1 << 10)


async def test_get_base_permissions_ignores_unknown_role_id(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_role(db_conn, role_id=1, guild_id=1, permissions=1 << 10)

    result = await queries.get_base_permissions(db_conn, guild_id=1, role_ids=[999999])

    assert result == (1 << 10)


async def test_get_visibility_channels_excludes_categories_and_voice(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_channel(db_conn, channel_id=10, guild_id=1, type=0)
    await _seed_channel(db_conn, channel_id=20, guild_id=1, type=4)  # category
    await _seed_channel(db_conn, channel_id=30, guild_id=1, type=2)  # voice
    await _seed_channel(db_conn, channel_id=31, guild_id=1, type=13)  # stage voice

    rows = await queries.get_visibility_channels(db_conn, guild_id=1)

    assert [r["id"] for r in rows] == [10]


async def test_get_visibility_channels_includes_parent_id(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_channel(db_conn, channel_id=20, guild_id=1, type=4)
    await _seed_channel(db_conn, channel_id=10, guild_id=1, type=0, parent_id=20)

    rows = await queries.get_visibility_channels(db_conn, guild_id=1)

    assert rows == [{"id": 10, "parent_id": 20}]


async def test_get_visibility_channels_scoped_to_guild(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_guild(db_conn, guild_id=2)
    await _seed_channel(db_conn, channel_id=10, guild_id=1)
    await _seed_channel(db_conn, channel_id=11, guild_id=2)

    rows = await queries.get_visibility_channels(db_conn, guild_id=1)

    assert [r["id"] for r in rows] == [10]


async def test_get_channel_role_overwrites_filters_by_channel_and_role(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_channel(db_conn, channel_id=10, guild_id=1)
    await _seed_channel(db_conn, channel_id=11, guild_id=1)
    await _seed_role(db_conn, role_id=1, guild_id=1)
    await _seed_role(db_conn, role_id=42, guild_id=1)
    await _seed_channel_role_overwrite(db_conn, channel_id=10, role_id=1, allow=5)
    await _seed_channel_role_overwrite(db_conn, channel_id=10, role_id=42, deny=5)
    await _seed_channel_role_overwrite(db_conn, channel_id=11, role_id=1, allow=9)

    rows = await queries.get_channel_role_overwrites(db_conn, channel_ids=[10], role_ids=[1])

    assert [(r["channel_id"], r["role_id"]) for r in rows] == [(10, 1)]


async def test_get_channel_role_overwrites_returns_empty_for_empty_input(db_conn):
    assert await queries.get_channel_role_overwrites(db_conn, channel_ids=[], role_ids=[1]) == []
    assert await queries.get_channel_role_overwrites(db_conn, channel_ids=[10], role_ids=[]) == []


async def test_get_channel_member_overwrites_filters_by_channel_and_user(db_conn):
    await _seed_guild(db_conn, guild_id=1)
    await _seed_channel(db_conn, channel_id=10, guild_id=1)
    await _seed_channel(db_conn, channel_id=11, guild_id=1)
    await _seed_user(db_conn, user_id=7, display_name="a")
    await _seed_user(db_conn, user_id=8, display_name="b")
    await _seed_channel_member_overwrite(db_conn, channel_id=10, user_id=7, deny=5)
    await _seed_channel_member_overwrite(db_conn, channel_id=10, user_id=8, deny=5)
    await _seed_channel_member_overwrite(db_conn, channel_id=11, user_id=7, deny=5)

    rows = await queries.get_channel_member_overwrites(db_conn, channel_ids=[10], user_id=7)

    assert [(r["channel_id"],) for r in rows] == [(10,)]


async def test_get_channel_member_overwrites_returns_empty_for_empty_channel_ids(db_conn):
    assert await queries.get_channel_member_overwrites(db_conn, channel_ids=[], user_id=7) == []
