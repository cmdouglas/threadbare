from threadbare.db import queries
from threadbare.rendering.quotes import render_reply_quote


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, 'general')",
        (channel_id, guild_id),
    )


async def _seed_user(conn, *, user_id, display_name):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (user_id, display_name),
    )


async def _seed_message(
    conn, *, message_id, channel_id, author_id, content="hello", reply_to_id=None
):
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, reply_to_id, posted_at)
        VALUES (%s, %s, %s, %s, %s, now())
        """,
        (message_id, channel_id, author_id, content, reply_to_id),
    )


async def test_render_reply_quote_for_a_real_reply_chain(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=1, display_name="alice")
    await _seed_user(db_conn, user_id=2, display_name="bob")
    await _seed_message(db_conn, message_id=100, channel_id=10, author_id=1, content="original")
    await _seed_message(
        db_conn, message_id=101, channel_id=10, author_id=2, content="a reply", reply_to_id=100
    )
    message_row = await queries.get_message_for_render(db_conn, 101)

    html = await render_reply_quote(db_conn, message_row)

    assert html is not None
    assert 'data-quoted-message-id="100"' in html
    assert "alice" in html
    assert "original" in html


async def test_render_reply_quote_returns_none_when_not_a_reply(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=1, display_name="alice")
    await _seed_message(db_conn, message_id=100, channel_id=10, author_id=1)
    message_row = await queries.get_message_for_render(db_conn, 100)

    assert await render_reply_quote(db_conn, message_row) is None


async def test_render_reply_quote_returns_none_when_target_no_longer_exists(db_conn):
    # reply_to_id is ON DELETE SET NULL, so a genuinely deleted target can
    # never be observed here -- this instead simulates the FK pointing at an
    # id get_message_for_render can't join to (defensive: no crash either way).
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=1, display_name="alice")
    await _seed_message(db_conn, message_id=101, channel_id=10, author_id=1, content="a reply")
    message_row = await queries.get_message_for_render(db_conn, 101)
    message_row["reply_to_id"] = 999999  # no such message

    assert await render_reply_quote(db_conn, message_row) is None
