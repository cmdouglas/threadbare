from threadbare.rendering.markdown import ReferencedIds
from threadbare.rendering.resolve import build_resolved_refs


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name) VALUES (%s, %s, 0, 'general')",
        (channel_id, guild_id),
    )


async def test_build_resolved_refs_batches_user_and_channel_lookups(db_conn):
    await _seed_guild_and_channel(db_conn)
    await db_conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s), (%s, %s)",
        (1, "alice", 2, "bob"),
    )

    ids = ReferencedIds(
        user_ids=frozenset({1, 2, 999}), role_ids=frozenset({5}), channel_ids=frozenset({10, 888})
    )

    refs = await build_resolved_refs(db_conn, ids)

    assert refs.users == {1: "alice", 2: "bob"}
    assert refs.channels == {10: "general"}
    assert refs.roles == {}


async def test_build_resolved_refs_handles_no_referenced_ids(db_conn):
    ids = ReferencedIds(user_ids=frozenset(), role_ids=frozenset(), channel_ids=frozenset())

    refs = await build_resolved_refs(db_conn, ids)

    assert refs.users == {}
    assert refs.channels == {}
    assert refs.roles == {}
