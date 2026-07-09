from threadbare.sync_worker import repository


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10, is_public=False):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s)",
        (guild_id, "Test Guild"),
    )
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, 0, 'general', %s)
        """,
        (channel_id, guild_id, is_public),
    )


async def _seed_message(conn, *, message_id, channel_id, author_id=100):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (author_id, "someone"),
    )
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (message_id, channel_id, author_id, "hello"),
    )


async def test_delete_message_removes_the_row(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    await repository.delete_message(db_conn, 1000)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE id = 1000")
        assert (await cur.fetchone())["n"] == 0


async def test_delete_message_is_a_no_op_for_unknown_id(db_conn):
    await repository.delete_message(db_conn, 999999)  # should not raise


async def test_delete_messages_removes_multiple_rows(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1001, channel_id=10)
    await _seed_message(db_conn, message_id=1002, channel_id=10)
    await _seed_message(db_conn, message_id=1003, channel_id=10)

    await repository.delete_messages(db_conn, [1001, 1002])

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT id FROM messages ORDER BY id")
        remaining = {row["id"] for row in await cur.fetchall()}
    assert remaining == {1003}


async def test_get_channel_is_public_returns_none_for_unknown_channel(db_conn):
    assert await repository.get_channel_is_public(db_conn, 999) is None


async def test_get_and_set_channel_is_public(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=False)

    assert await repository.get_channel_is_public(db_conn, 10) is False

    await repository.set_channel_is_public(db_conn, 10, True)

    assert await repository.get_channel_is_public(db_conn, 10) is True


async def test_purge_channel_content_removes_messages_and_cascades(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (100, "someone"))
    await db_conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (1000, 10, 100, "hello"),
    )
    await db_conn.execute(
        """
        INSERT INTO attachments (id, message_id, filename, size, cached_url, url_expires_at)
        VALUES (%s, %s, %s, %s, %s, now())
        """,
        (2000, 1000, "file.png", 123, "https://example.com/file.png"),
    )
    await db_conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, now())",
        (3000, 10, "a thread"),
    )
    await db_conn.execute(
        """
        INSERT INTO messages (id, thread_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (4000, 3000, 100, "in a thread"),
    )

    await repository.purge_channel_content(db_conn, 10)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages")
        assert (await cur.fetchone())["n"] == 0
        await cur.execute("SELECT count(*) AS n FROM attachments")
        assert (await cur.fetchone())["n"] == 0
        await cur.execute("SELECT count(*) AS n FROM threads")
        assert (await cur.fetchone())["n"] == 0
        # The channel row itself is untouched — only its content is purged.
        await cur.execute("SELECT count(*) AS n FROM channels WHERE id = 10")
        assert (await cur.fetchone())["n"] == 1
