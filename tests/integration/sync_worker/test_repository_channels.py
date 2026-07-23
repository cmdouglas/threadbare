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


async def _seed_guild_channel_role_and_user(db_conn):
    await repository.upsert_guild(db_conn, {"id": 1, "name": "Test Guild", "icon": None})
    await repository.upsert_channel(db_conn, _channel_row())
    await repository.upsert_role(
        db_conn,
        {"id": 500, "guild_id": 1, "name": "Mods", "color": 0, "position": 1, "permissions": 0},
    )
    await repository.upsert_user(
        db_conn,
        {
            "id": 900,
            "display_name": "someone",
            "avatar_hash": None,
            "is_bot": False,
            "role_ids": [],
        },
    )


async def test_sync_channel_role_overwrites_inserts_rows(db_conn):
    await _seed_guild_channel_role_and_user(db_conn)

    await repository.sync_channel_role_overwrites(
        db_conn, 10, [{"channel_id": 10, "role_id": 500, "allow": 0x400, "deny": 0x800}]
    )

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT role_id, allow, deny FROM channel_role_overwrites WHERE channel_id = 10"
        )
        rows = await cur.fetchall()
    assert rows == [{"role_id": 500, "allow": 0x400, "deny": 0x800}]


async def test_sync_channel_role_overwrites_replaces_prior_rows_exactly(db_conn):
    await _seed_guild_channel_role_and_user(db_conn)
    await repository.sync_channel_role_overwrites(
        db_conn, 10, [{"channel_id": 10, "role_id": 500, "allow": 0x400, "deny": 0x800}]
    )

    # A later call with an empty list means "no overwrites anymore" -- the
    # previously-stored row must be gone, not left behind.
    await repository.sync_channel_role_overwrites(db_conn, 10, [])

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM channel_role_overwrites WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 0


async def test_sync_channel_role_overwrites_cascades_on_role_delete(db_conn):
    await _seed_guild_channel_role_and_user(db_conn)
    await repository.sync_channel_role_overwrites(
        db_conn, 10, [{"channel_id": 10, "role_id": 500, "allow": 0x400, "deny": 0x800}]
    )

    await repository.delete_role(db_conn, 500)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM channel_role_overwrites WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 0


async def test_sync_channel_member_overwrites_inserts_rows(db_conn):
    await _seed_guild_channel_role_and_user(db_conn)

    await repository.sync_channel_member_overwrites(
        db_conn, 10, [{"channel_id": 10, "user_id": 900, "allow": 0x1, "deny": 0x2}]
    )

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT user_id, allow, deny FROM channel_member_overwrites WHERE channel_id = 10"
        )
        rows = await cur.fetchall()
    assert rows == [{"user_id": 900, "allow": 0x1, "deny": 0x2}]


async def test_sync_channel_member_overwrites_replaces_prior_rows_exactly(db_conn):
    await _seed_guild_channel_role_and_user(db_conn)
    await repository.sync_channel_member_overwrites(
        db_conn, 10, [{"channel_id": 10, "user_id": 900, "allow": 0x1, "deny": 0x2}]
    )

    await repository.sync_channel_member_overwrites(db_conn, 10, [])

    async with db_conn.cursor() as cur:
        await cur.execute(
            "SELECT count(*) AS n FROM channel_member_overwrites WHERE channel_id = 10"
        )
        assert (await cur.fetchone())["n"] == 0
