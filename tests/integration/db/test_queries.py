from datetime import UTC, datetime

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


async def _seed_user(conn, *, user_id, display_name):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (user_id, display_name),
    )


async def _seed_message(conn, *, message_id, channel_id, author_id, content="hello"):
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (message_id, channel_id, author_id, content),
    )


async def test_get_message_for_render_returns_message_with_author_display_name(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=100, display_name="alice")
    await _seed_message(db_conn, message_id=1000, channel_id=10, author_id=100, content="hi there")

    row = await queries.get_message_for_render(db_conn, 1000)

    assert row["id"] == 1000
    assert row["content"] == "hi there"
    assert row["author_id"] == 100
    assert row["author_display_name"] == "alice"
    assert row["channel_id"] == 10
    assert row["thread_id"] is None
    assert row["reply_to_id"] is None


async def test_get_message_for_render_returns_none_for_unknown_message(db_conn):
    assert await queries.get_message_for_render(db_conn, 999999) is None


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
