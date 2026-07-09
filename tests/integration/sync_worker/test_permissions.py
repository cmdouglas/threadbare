from dataclasses import dataclass

from threadbare.sync_worker import repository
from threadbare.sync_worker.permissions import (
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    refresh_channel_public_status,
)

BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


@dataclass
class Overwrite:
    allow: int = 0
    deny: int = 0


async def _seed_guild_and_channel(conn, *, guild_id=1, channel_id=10, is_public=False):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (guild_id, "Test Guild"))
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, 0, 'general', %s)
        """,
        (channel_id, guild_id, is_public),
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
