from threadbare.sync_worker import repository


async def test_upsert_guild_creates_the_row(db_conn):
    await repository.upsert_guild(db_conn, {"id": 1, "name": "Test Guild", "icon": None})

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name FROM guilds WHERE id = 1")
        assert (await cur.fetchone())["name"] == "Test Guild"


async def test_upsert_guild_updates_name_on_conflict(db_conn):
    await repository.upsert_guild(db_conn, {"id": 1, "name": "Old Name", "icon": None})
    await repository.upsert_guild(db_conn, {"id": 1, "name": "New Name", "icon": None})

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name FROM guilds WHERE id = 1")
        assert (await cur.fetchone())["name"] == "New Name"


def _channel_row(**overrides):
    row = {
        "id": 10,
        "guild_id": 1,
        "parent_id": None,
        "type": 0,
        "name": "general",
        "position": 0,
        "topic": None,
    }
    row.update(overrides)
    return row


async def test_upsert_channel_creates_row_with_defaults(db_conn):
    await repository.upsert_guild(db_conn, {"id": 1, "name": "Test Guild", "icon": None})

    await repository.upsert_channel(db_conn, _channel_row())

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name, is_public, indexed FROM channels WHERE id = 10")
        row = await cur.fetchone()
    assert row["name"] == "general"
    assert row["is_public"] is False  # schema default; discovery computes this separately
    assert row["indexed"] is True  # schema default


async def test_upsert_channel_updates_metadata_on_conflict(db_conn):
    await repository.upsert_guild(db_conn, {"id": 1, "name": "Test Guild", "icon": None})
    await repository.upsert_channel(db_conn, _channel_row(name="general", topic="old topic"))

    await repository.upsert_channel(
        db_conn, _channel_row(name="general-renamed", topic="new topic")
    )

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name, topic FROM channels WHERE id = 10")
        row = await cur.fetchone()
    assert row["name"] == "general-renamed"
    assert row["topic"] == "new topic"


async def test_upsert_channel_never_touches_is_public_or_indexed_on_conflict(db_conn):
    await repository.upsert_guild(db_conn, {"id": 1, "name": "Test Guild", "icon": None})
    await repository.upsert_channel(db_conn, _channel_row())
    # Simulate the state after refresh_channel_public_status ran, and a mod
    # explicitly un-indexing the channel via the (future) admin page.
    await repository.set_channel_is_public(db_conn, 10, True)
    await db_conn.execute("UPDATE channels SET indexed = false WHERE id = 10")

    await repository.upsert_channel(db_conn, _channel_row(name="renamed"))

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name, is_public, indexed FROM channels WHERE id = 10")
        row = await cur.fetchone()
    assert row["name"] == "renamed"
    assert row["is_public"] is True
    assert row["indexed"] is False


async def test_upsert_channel_inserts_with_indexed_false_when_passed(db_conn):
    await repository.upsert_guild(db_conn, {"id": 1, "name": "Test Guild", "icon": None})

    await repository.upsert_channel(db_conn, _channel_row(), indexed=False)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT indexed FROM channels WHERE id = 10")
        assert (await cur.fetchone())["indexed"] is False


async def test_upsert_channel_ignores_the_indexed_param_on_conflict(db_conn):
    await repository.upsert_guild(db_conn, {"id": 1, "name": "Test Guild", "icon": None})
    await repository.upsert_channel(db_conn, _channel_row(), indexed=True)
    await db_conn.execute("UPDATE channels SET indexed = false WHERE id = 10")

    # A mod un-indexed this channel; a later rediscovery pass must not
    # revert that, even if it happens to pass indexed=True again.
    await repository.upsert_channel(db_conn, _channel_row(name="renamed"), indexed=True)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT name, indexed FROM channels WHERE id = 10")
        row = await cur.fetchone()
    assert row["name"] == "renamed"
    assert row["indexed"] is False


async def test_get_auto_index_new_channels_defaults_to_true_with_no_row(db_conn):
    assert await repository.get_auto_index_new_channels(db_conn) is True


async def test_get_auto_index_new_channels_returns_the_stored_value(db_conn):
    await db_conn.execute(
        "INSERT INTO site_settings (id, auto_index_new_channels) VALUES (true, false)"
    )

    assert await repository.get_auto_index_new_channels(db_conn) is False
