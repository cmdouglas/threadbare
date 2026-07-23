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


def _message_row(**overrides):
    row = {
        "id": 2000,
        "channel_id": 10,
        "thread_id": None,
        "author_id": 100,
        "content": "",
        "reply_to_id": None,
        "posted_at": datetime(2026, 1, 1, tzinfo=UTC),
        "edited_at": None,
        "flags": 0,
        "type": 0,
    }
    row.update(overrides)
    return row


async def test_upsert_message_persists_type(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (100, "someone"))

    await repository.upsert_message(db_conn, _message_row(type=7))

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT type FROM messages WHERE id = %s", (2000,))
        assert (await cur.fetchone())["type"] == 7


async def test_upsert_message_type_is_updated_on_conflict(db_conn):
    # Unlike posted_at/author_id (genuinely immutable facts), type is
    # updated on every upsert -- it's a Discord-side fact that can never
    # actually change after a message is created, but this project didn't
    # capture it before migration 0006, so every pre-existing row started
    # out wrong (defaulted to 0). Re-including it here is what lets a
    # re-backfill actually repair historical rows; excluding it (as an
    # earlier version of this function did) would make that impossible.
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (100, "someone"))
    await repository.upsert_message(db_conn, _message_row(type=0))

    await repository.upsert_message(db_conn, _message_row(type=7, content="edited"))

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT type, content FROM messages WHERE id = %s", (2000,))
        result = await cur.fetchone()
        assert result["type"] == 7
        assert result["content"] == "edited"


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


def _embed_row(*, message_id, position=0, title="a title", fields=None, video_url=None):
    return {
        "message_id": message_id,
        "position": position,
        "type": "rich",
        "title": title,
        "description": None,
        "url": None,
        "color": None,
        "author_name": None,
        "author_url": None,
        "footer_text": None,
        "image_url": None,
        "thumbnail_url": None,
        "video_url": video_url,
        "fields": fields or [],
    }


async def test_sync_message_embeds_inserts_rows(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    await repository.sync_message_embeds(
        db_conn,
        1000,
        [
            _embed_row(message_id=1000, position=0, title="first"),
            _embed_row(message_id=1000, position=1, title="second"),
        ],
    )

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT title FROM embeds WHERE message_id = 1000 ORDER BY position")
        rows = await cur.fetchall()
    assert [row["title"] for row in rows] == ["first", "second"]


async def test_sync_message_embeds_stores_video_url(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    await repository.sync_message_embeds(
        db_conn,
        1000,
        [_embed_row(message_id=1000, video_url="https://example.com/clip.mp4")],
    )

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT video_url FROM embeds WHERE message_id = 1000")
        row = await cur.fetchone()
    assert row["video_url"] == "https://example.com/clip.mp4"


async def test_sync_message_embeds_stores_fields_as_json(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    await repository.sync_message_embeds(
        db_conn,
        1000,
        [_embed_row(message_id=1000, fields=[{"name": "k", "value": "v", "inline": True}])],
    )

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT fields FROM embeds WHERE message_id = 1000")
        row = await cur.fetchone()
    assert row["fields"] == [{"name": "k", "value": "v", "inline": True}]


async def test_sync_message_embeds_replaces_existing_rows_on_resync(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.sync_message_embeds(
        db_conn, 1000, [_embed_row(message_id=1000, position=0, title="original")]
    )

    # A re-fetched Message (edit) now has a different, single embed.
    await repository.sync_message_embeds(
        db_conn, 1000, [_embed_row(message_id=1000, position=0, title="replaced")]
    )

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT title FROM embeds WHERE message_id = 1000")
        rows = await cur.fetchall()
    assert [row["title"] for row in rows] == ["replaced"]


async def test_sync_message_embeds_clears_all_rows_when_given_empty_list(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.sync_message_embeds(db_conn, 1000, [_embed_row(message_id=1000)])

    await repository.sync_message_embeds(db_conn, 1000, [])

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM embeds WHERE message_id = 1000")
        assert (await cur.fetchone())["n"] == 0


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


async def test_message_exists_returns_false_for_unknown_message(db_conn):
    assert await repository.message_exists(db_conn, 999999) is False


async def test_message_exists_returns_true_for_known_message(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    assert await repository.message_exists(db_conn, 1000) is True


async def test_increment_reaction_inserts_a_new_row_at_count_1(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    await repository.increment_reaction(db_conn, message_id=1000, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT count FROM reactions WHERE message_id = 1000 AND emoji = %s", ("👍",)
        )
        assert (await cur.fetchone())["count"] == 1


async def test_increment_reaction_increments_an_existing_row(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.increment_reaction(db_conn, message_id=1000, emoji="👍")

    await repository.increment_reaction(db_conn, message_id=1000, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT count FROM reactions WHERE message_id = 1000 AND emoji = %s", ("👍",)
        )
        assert (await cur.fetchone())["count"] == 2


async def test_decrement_reaction_decrements_an_existing_row(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.increment_reaction(db_conn, message_id=1000, emoji="👍")
    await repository.increment_reaction(db_conn, message_id=1000, emoji="👍")

    await repository.decrement_reaction(db_conn, message_id=1000, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT count FROM reactions WHERE message_id = 1000 AND emoji = %s", ("👍",)
        )
        assert (await cur.fetchone())["count"] == 1


async def test_decrement_reaction_deletes_the_row_when_count_reaches_zero(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.increment_reaction(db_conn, message_id=1000, emoji="👍")

    await repository.decrement_reaction(db_conn, message_id=1000, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT count(*) AS n FROM reactions WHERE message_id = 1000 AND emoji = %s", ("👍",)
        )
        assert (await cur.fetchone())["n"] == 0


async def test_decrement_reaction_is_a_no_op_for_an_unknown_row(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    await repository.decrement_reaction(db_conn, message_id=1000, emoji="👍")  # should not raise

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM reactions WHERE message_id = 1000")
        assert (await cur.fetchone())["n"] == 0


async def test_clear_reactions_removes_all_rows_for_a_message(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.increment_reaction(db_conn, message_id=1000, emoji="👍")
    await repository.increment_reaction(db_conn, message_id=1000, emoji="🎉")

    await repository.clear_reactions(db_conn, 1000)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM reactions WHERE message_id = 1000")
        assert (await cur.fetchone())["n"] == 0


async def test_clear_reactions_is_a_no_op_for_a_message_with_no_reactions(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    await repository.clear_reactions(db_conn, 1000)  # should not raise


async def test_clear_reaction_emoji_removes_only_the_given_emoji(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.increment_reaction(db_conn, message_id=1000, emoji="👍")
    await repository.increment_reaction(db_conn, message_id=1000, emoji="🎉")

    await repository.clear_reaction_emoji(db_conn, message_id=1000, emoji="👍")

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT emoji FROM reactions WHERE message_id = 1000")
        remaining = {row["emoji"] for row in await cur.fetchall()}
    assert remaining == {"🎉"}


async def test_sync_message_reactions_inserts_new_rows(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)

    await repository.sync_message_reactions(db_conn, 1000, [("👍", 3), ("🎉", 1)])

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT emoji, count FROM reactions WHERE message_id = 1000 ORDER BY emoji"
        )
        rows = await cur.fetchall()
    assert {(row["emoji"], row["count"]) for row in rows} == {("👍", 3), ("🎉", 1)}


async def test_sync_message_reactions_updates_existing_counts(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.sync_message_reactions(db_conn, 1000, [("👍", 3)])

    await repository.sync_message_reactions(db_conn, 1000, [("👍", 5)])

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT count FROM reactions WHERE message_id = 1000 AND emoji = %s", ("👍",)
        )
        assert (await cur.fetchone())["count"] == 5


async def test_sync_message_reactions_deletes_rows_no_longer_present(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.sync_message_reactions(db_conn, 1000, [("👍", 3), ("🎉", 1)])

    await repository.sync_message_reactions(db_conn, 1000, [("👍", 3)])

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT emoji FROM reactions WHERE message_id = 1000")
        remaining = {row["emoji"] for row in await cur.fetchall()}
    assert remaining == {"👍"}


async def test_sync_message_reactions_with_empty_list_clears_all_rows(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await _seed_message(db_conn, message_id=1000, channel_id=10)
    await repository.sync_message_reactions(db_conn, 1000, [("👍", 3)])

    await repository.sync_message_reactions(db_conn, 1000, [])

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM reactions WHERE message_id = 1000")
        assert (await cur.fetchone())["n"] == 0


async def test_channel_exists_returns_true_for_known_channel(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)

    assert await repository.channel_exists(db_conn, 10) is True


async def test_channel_exists_returns_false_for_unknown_channel(db_conn):
    assert await repository.channel_exists(db_conn, 999999) is False


async def test_get_content_channel_ids_excludes_categories(db_conn):
    await db_conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, 'general')", (10, 1)
    )
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 4, 'a category')", (11, 1)
    )

    ids = await repository.get_content_channel_ids(db_conn)

    assert 10 in ids
    assert 11 not in ids


async def test_reset_thread_checkpoints_for_channel_resets_only_that_channels_threads(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, 'other')", (20, 1)
    )
    await _seed_thread(db_conn, thread_id=3000, parent_channel_id=10)
    await _seed_thread(db_conn, thread_id=3001, parent_channel_id=20)
    await repository.set_thread_backfill_checkpoint(
        db_conn, 3000, last_message_id=500, complete=True
    )
    await repository.set_thread_backfill_checkpoint(
        db_conn, 3001, last_message_id=600, complete=True
    )

    reset_count = await repository.reset_thread_checkpoints_for_channel(db_conn, 10)

    assert reset_count == 1
    assert await repository.get_thread_backfill_checkpoint(db_conn, 3000) is None
    assert await repository.get_thread_backfill_checkpoint(db_conn, 3001) == 600
