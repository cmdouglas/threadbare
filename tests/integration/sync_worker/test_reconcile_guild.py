from dataclasses import dataclass, field
from datetime import UTC, datetime

import discord

from threadbare.db.pool import create_pool
from threadbare.sync_worker.reconciliation import reconcile_guild


@dataclass
class FakeAuthor:
    id: int
    display_name: str = "someone"
    avatar: object | None = None


@dataclass
class FakeMessage:
    id: int
    author: FakeAuthor
    content: str = "hello"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    edited_at: datetime | None = None
    reference: object | None = None
    attachments: list = field(default_factory=list)
    reactions: list = field(default_factory=list)


class FakeChannel:
    def __init__(self, id, type=discord.ChannelType.text):
        self.id = id
        self.type = type


class FakeGuild:
    def __init__(self, channels):
        self._channels = channels

    async def fetch_channels(self):
        return self._channels

    async def active_threads(self):
        # reconcile_guild() also runs reconcile_guild_threads(), which needs
        # this — no threads are relevant to this regression test.
        return []


class FakeClient:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild

    async def fetch_guild(self, guild_id):
        return self._guild


class ChannelKeyedFetcher:
    """Serves a fixed page per channel_id. Raises if ever called for an id
    in `forbidden_ids` — used to prove reconcile_channel() is never invoked
    against a forum channel's nonexistent top-level history (the bug this
    test guards against).
    """

    def __init__(self, pages_by_channel: dict[int, list[FakeMessage]], *, forbidden_ids=()):
        self._pages_by_channel = pages_by_channel
        self._forbidden_ids = set(forbidden_ids)
        self.calls: list[int] = []

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        if channel_id in self._forbidden_ids:
            raise AssertionError(f"reconcile_channel() must never fetch channel {channel_id}")
        self.calls.append(channel_id)
        return self._pages_by_channel.get(channel_id, []) if after is None else []


async def _cleanup(conn):
    await conn.execute("DELETE FROM messages")
    await conn.execute("DELETE FROM thread_sync_state")
    await conn.execute("DELETE FROM threads")
    await conn.execute("DELETE FROM sync_state")
    await conn.execute("DELETE FROM channels")
    await conn.execute("DELETE FROM guilds")
    await conn.execute("DELETE FROM users")
    await conn.commit()


async def _seed_channel(conn, *, guild_id, channel_id, is_public, type=0):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (guild_id, "Test Guild"),
    )
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (channel_id, guild_id, type, f"chan-{channel_id}", is_public),
    )


async def test_reconcile_guild_never_reconciles_a_public_forum_channels_top_level_history(
    db_conn, test_database_url
):
    """Regression test for a real bug found by inspection: once forum
    channels' is_public computes normally (the forum-channel branch), a
    public+indexed forum channel must still never have reconcile_channel()
    called against it — it has no top-level messages of its own, all
    content lives in child threads (reconciled separately).
    """
    await _seed_channel(db_conn, guild_id=1, channel_id=10, is_public=True)
    await _seed_channel(
        db_conn, guild_id=1, channel_id=20, is_public=True, type=discord.ChannelType.forum.value
    )
    await db_conn.commit()

    author = FakeAuthor(id=1)
    fetcher = ChannelKeyedFetcher({10: [FakeMessage(id=100, author=author)]}, forbidden_ids={20})
    guild = FakeGuild([FakeChannel(10), FakeChannel(20, type=discord.ChannelType.forum)])
    client = FakeClient(guild)

    pool = create_pool(test_database_url)
    await pool.open()
    try:
        await reconcile_guild(client, pool, guild_id=1, fetcher=fetcher)
    finally:
        await pool.close()

    assert 10 in fetcher.calls
    assert 20 not in fetcher.calls

    await _cleanup(db_conn)
