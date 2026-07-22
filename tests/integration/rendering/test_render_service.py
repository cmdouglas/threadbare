from datetime import UTC, datetime

from threadbare.db import queries
from threadbare.rendering.render_service import render_message_for_display

EXPIRES_AT = datetime(2026, 1, 2, tzinfo=UTC)


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


async def test_render_message_for_display_with_every_optional_piece_present(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=1, display_name="alice")
    await _seed_user(db_conn, user_id=2, display_name="bob")
    await _seed_message(db_conn, message_id=100, channel_id=10, author_id=1, content="original")
    await _seed_message(
        db_conn,
        message_id=101,
        channel_id=10,
        author_id=2,
        content="hi <@1>, check this out",
        reply_to_id=100,
    )
    await db_conn.execute(
        """
        INSERT INTO attachments (
            id, message_id, filename, content_type, size, cached_url, url_expires_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (2000, 101, "cat.png", "image/png", 1024, "https://cdn.example/cat.png", EXPIRES_AT),
    )
    await db_conn.execute(
        "INSERT INTO embeds (message_id, position, title) VALUES (%s, %s, %s)",
        (101, 0, "a linked page"),
    )
    await db_conn.execute(
        "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (101, "👍", 3)
    )
    await db_conn.execute(
        "INSERT INTO reactions (message_id, emoji, count) VALUES (%s, %s, %s)", (101, "🎉", 1)
    )

    message_row = await queries.get_message_for_render(db_conn, 101)
    rendered = await render_message_for_display(db_conn, message_row)

    assert '<span class="mention mention-user">@alice</span>' in rendered.content_html
    assert rendered.reply_quote_html is not None
    assert "original" in rendered.reply_quote_html
    assert 'data-quoted-message-id="100"' in rendered.reply_quote_html
    assert "cat.png" in rendered.attachments_html
    assert "a linked page" in rendered.embeds_html
    assert "👍" in rendered.reactions_html
    assert "🎉" in rendered.reactions_html


async def test_render_message_for_display_threads_script_root_through_attachments_and_quotes(
    db_conn,
):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=1, display_name="alice")
    await _seed_user(db_conn, user_id=2, display_name="bob")
    await _seed_message(db_conn, message_id=100, channel_id=10, author_id=1, content="original")
    await _seed_message(
        db_conn, message_id=101, channel_id=10, author_id=2, content="a reply", reply_to_id=100
    )
    await db_conn.execute(
        """
        INSERT INTO attachments (
            id, message_id, filename, content_type, size, cached_url, url_expires_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (2000, 101, "cat.png", "image/png", 1024, "https://cdn.example/cat.png", EXPIRES_AT),
    )

    message_row = await queries.get_message_for_render(db_conn, 101)
    rendered = await render_message_for_display(db_conn, message_row, script_root="/mirror")

    assert 'href="/mirror/board/10/continuous/page/1#post-100"' in rendered.reply_quote_html
    assert 'href="/mirror/att/2000"' in rendered.attachments_html


async def test_render_message_for_display_with_no_optional_pieces(db_conn):
    await _seed_guild_and_channel(db_conn)
    await _seed_user(db_conn, user_id=1, display_name="alice")
    await _seed_message(db_conn, message_id=100, channel_id=10, author_id=1, content="just text")

    message_row = await queries.get_message_for_render(db_conn, 100)
    rendered = await render_message_for_display(db_conn, message_row)

    assert rendered.content_html == "just text"
    assert rendered.reply_quote_html is None
    assert rendered.attachments_html == ""
    assert rendered.embeds_html == ""
    assert rendered.reactions_html == ""
