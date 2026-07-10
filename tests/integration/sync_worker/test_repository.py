from datetime import UTC, datetime

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


async def test_get_channel_sync_flags_returns_none_for_unknown_channel(db_conn):
    assert await repository.get_channel_sync_flags(db_conn, 999) is None


async def test_get_channel_sync_flags_returns_is_public_and_indexed(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)

    assert await repository.get_channel_sync_flags(db_conn, 10) == (True, True)


async def test_get_channel_is_public_returns_none_for_unknown_channel(db_conn):
    assert await repository.get_channel_is_public(db_conn, 999) is None


async def test_get_and_set_channel_is_public(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=False)

    assert await repository.get_channel_is_public(db_conn, 10) is False

    await repository.set_channel_is_public(db_conn, 10, True)

    assert await repository.get_channel_is_public(db_conn, 10) is True


async def _seed_thread(conn, *, thread_id, parent_channel_id):
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, now())",
        (thread_id, parent_channel_id, "a thread"),
    )


async def test_get_thread_backfill_checkpoint_returns_none_for_unknown_thread(db_conn):
    assert await repository.get_thread_backfill_checkpoint(db_conn, 999) is None


async def test_set_and_get_thread_backfill_checkpoint(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)

    await repository.set_thread_backfill_checkpoint(
        db_conn, 3000, last_message_id=500, complete=False
    )

    assert await repository.get_thread_backfill_checkpoint(db_conn, 3000) == 500

    await repository.set_thread_backfill_checkpoint(
        db_conn, 3000, last_message_id=600, complete=True
    )

    assert await repository.get_thread_backfill_checkpoint(db_conn, 3000) == 600


async def test_upsert_thread_inserts_a_new_row(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    await repository.upsert_thread(
        db_conn,
        {
            "id": 3000,
            "parent_channel_id": 10,
            "name": "a thread",
            "archived": False,
            "created_at": created_at,
            "message_count": 5,
        },
    )

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT parent_channel_id, name, archived, created_at, message_count "
            "FROM threads WHERE id = 3000"
        )
        row = await cur.fetchone()
    assert row["parent_channel_id"] == 10
    assert row["name"] == "a thread"
    assert row["archived"] is False
    assert row["created_at"] == created_at
    assert row["message_count"] == 5


async def test_upsert_thread_updates_mutable_fields_but_not_parent_or_created_at(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    original_created_at = datetime(2026, 1, 1, tzinfo=UTC)
    await repository.upsert_thread(
        db_conn,
        {
            "id": 3000,
            "parent_channel_id": 10,
            "name": "original name",
            "archived": False,
            "created_at": original_created_at,
            "message_count": 1,
        },
    )

    await repository.upsert_thread(
        db_conn,
        {
            "id": 3000,
            "parent_channel_id": 10,
            "name": "renamed",
            "archived": True,
            "created_at": datetime(2026, 6, 1, tzinfo=UTC),  # should be ignored on conflict
            "message_count": 9,
        },
    )

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT name, archived, created_at, message_count FROM threads WHERE id = 3000"
        )
        row = await cur.fetchone()
    assert row["name"] == "renamed"
    assert row["archived"] is True
    assert row["message_count"] == 9
    assert row["created_at"] == original_created_at


async def _seed_thread_message(conn, *, message_id, thread_id, author_id=100):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (author_id, "someone"),
    )
    await conn.execute(
        """
        INSERT INTO messages (id, thread_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (message_id, thread_id, author_id, "hello"),
    )


async def test_get_thread_message_ids_since_returns_ids_after_cutoff(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_thread_message(db_conn, message_id=101, thread_id=3000)
    await _seed_thread_message(db_conn, message_id=102, thread_id=3000)

    assert await repository.get_thread_message_ids_since(db_conn, 3000, 100) == {101, 102}
    assert await repository.get_thread_message_ids_since(db_conn, 3000, 101) == {102}


async def test_mark_thread_reconciled_sets_last_reconciled_at(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)

    await repository.mark_thread_reconciled(db_conn, 3000)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT last_reconciled_at FROM thread_sync_state WHERE thread_id = 3000")
        row = await cur.fetchone()
    assert row is not None
    assert row["last_reconciled_at"] is not None


async def test_delete_thread_removes_the_row_and_cascades(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_thread_message(db_conn, message_id=101, thread_id=3000)
    await repository.set_thread_backfill_checkpoint(
        db_conn, 3000, last_message_id=101, complete=True
    )

    await repository.delete_thread(db_conn, 3000)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM threads WHERE id = 3000")
        assert (await cur.fetchone())["n"] == 0
        await cur.execute("SELECT count(*) AS n FROM messages WHERE thread_id = 3000")
        assert (await cur.fetchone())["n"] == 0
        await cur.execute("SELECT count(*) AS n FROM thread_sync_state WHERE thread_id = 3000")
        assert (await cur.fetchone())["n"] == 0


async def test_delete_thread_is_a_no_op_for_unknown_id(db_conn):
    await repository.delete_thread(db_conn, 999999)  # should not raise


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
