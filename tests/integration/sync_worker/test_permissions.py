from dataclasses import dataclass

from threadbare.sync_worker import repository
from threadbare.sync_worker.permissions import (
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    refresh_channel_bot_access,
    refresh_channel_public_status,
)

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


@dataclass
class Overwrite:
    allow: int = 0
    deny: int = 0


async def _seed_guild_and_channel(
    conn, *, guild_id=1, channel_id=10, is_public=False, visibility_enrolled=False
):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, visibility_enrolled)
        VALUES (%s, %s, 0, 'general', %s, %s)
        """,
        (channel_id, guild_id, is_public, visibility_enrolled),
    )


async def test_refresh_sets_is_public_true_on_first_sight(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=False)

    result = await refresh_channel_public_status(
        db_conn,
        channel_id=10,
        default_role_permissions=BOTH_REQUIRED,
        category_overwrite=None,
        channel_overwrite=None,
    )

    assert result is True
    assert await repository.get_channel_is_public(db_conn, 10) is True


async def test_refresh_purges_content_when_channel_becomes_non_public(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (100, "someone"))
    await db_conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (1000, 10, 100, "hello"),
    )

    result = await refresh_channel_public_status(
        db_conn,
        channel_id=10,
        default_role_permissions=BOTH_REQUIRED,
        category_overwrite=None,
        channel_overwrite=Overwrite(deny=VIEW_CHANNEL),
    )

    assert result is False
    assert await repository.get_channel_is_public(db_conn, 10) is False
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 0


async def test_refresh_does_not_purge_when_staying_public(db_conn):
    await _seed_guild_and_channel(db_conn, is_public=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (100, "someone"))
    await db_conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (1000, 10, 100, "hello"),
    )

    result = await refresh_channel_public_status(
        db_conn,
        channel_id=10,
        default_role_permissions=BOTH_REQUIRED,
        category_overwrite=None,
        channel_overwrite=None,
    )

    assert result is True
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 1


async def test_refresh_does_not_purge_a_visibility_enrolled_channel_losing_public_access(db_conn):
    # A role-gated channel a mod has already enrolled is still meant to be
    # synced and filtered per-user at read time (should_sync) -- purging it
    # here just because @everyone lost access would silently undo that
    # enrollment and re-break the exact bug this predicate exists to fix.
    await _seed_guild_and_channel(db_conn, is_public=True, visibility_enrolled=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (100, "someone"))
    await db_conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (1000, 10, 100, "hello"),
    )

    result = await refresh_channel_public_status(
        db_conn,
        channel_id=10,
        default_role_permissions=BOTH_REQUIRED,
        category_overwrite=None,
        channel_overwrite=Overwrite(deny=VIEW_CHANNEL),
    )

    assert result is False
    assert await repository.get_channel_is_public(db_conn, 10) is False
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 1


async def test_refresh_channel_bot_access_sets_true_when_bot_has_required_permissions(db_conn):
    await _seed_guild_and_channel(db_conn)

    result = await refresh_channel_bot_access(db_conn, channel_id=10, bot_permissions=BOTH_REQUIRED)

    assert result is True
    assert await repository.get_channel_bot_can_read(db_conn, 10) is True


async def test_refresh_channel_bot_access_sets_false_when_bot_lacks_view_channel(db_conn):
    await _seed_guild_and_channel(db_conn)

    result = await refresh_channel_bot_access(
        db_conn, channel_id=10, bot_permissions=READ_MESSAGE_HISTORY
    )

    assert result is False
    assert await repository.get_channel_bot_can_read(db_conn, 10) is False


async def test_refresh_channel_bot_access_does_not_touch_is_public_or_content(db_conn):
    # bot_can_read is purely informational -- must never gate/purge synced
    # content the way refresh_channel_public_status's is_public transition
    # does, since a role-gated channel losing/regaining bot access has
    # nothing to do with whether members can see it.
    await _seed_guild_and_channel(db_conn, is_public=True, visibility_enrolled=True)
    await db_conn.execute("INSERT INTO users (id, display_name) VALUES (%s, %s)", (100, "someone"))
    await db_conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now())
        """,
        (1000, 10, 100, "hello"),
    )

    await refresh_channel_bot_access(db_conn, channel_id=10, bot_permissions=0)

    assert await repository.get_channel_is_public(db_conn, 10) is True
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM messages WHERE channel_id = 10")
        assert (await cur.fetchone())["n"] == 1
