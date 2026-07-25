from datetime import UTC, datetime, timedelta

from threadbare.discord_permissions import READ_MESSAGE_HISTORY, VIEW_CHANNEL
from threadbare.web.views import attachments as attachments_view

from .conftest import run


async def _seed_guild_and_channel(
    conn, *, guild_id=1, channel_id=10, is_public=True, indexed=True, visibility_enrolled=False
):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (guild_id, "Test Guild"),
    )
    await conn.execute(
        """
        INSERT INTO channels
            (id, guild_id, type, name, is_public, indexed, visibility_enrolled)
        VALUES (%s, %s, 0, 'general', %s, %s, %s)
        """,
        (channel_id, guild_id, is_public, indexed, visibility_enrolled),
    )


async def _seed_role(conn, *, role_id, guild_id=1, permissions=0):
    await conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (%s, %s, 'a role', 0, 0, %s)",
        (role_id, guild_id, permissions),
    )


async def _seed_thread(conn, *, thread_id, parent_channel_id):
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, now())",
        (thread_id, parent_channel_id, "a thread"),
    )


async def _seed_attachment(
    conn, *, attachment_id, message_id, channel_id=None, thread_id=None, url_expires_at
):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (100, "alice"),
    )
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, thread_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, %s, now())
        """,
        (message_id, channel_id, thread_id, 100, "hello"),
    )
    await conn.execute(
        """
        INSERT INTO attachments (
            id, message_id, filename, content_type, size, cached_url, url_expires_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            attachment_id,
            message_id,
            "cat.png",
            "image/png",
            100,
            "https://cdn.example/cat.png",
            url_expires_at,
        ),
    )


def test_attachment_proxy_returns_404_for_unknown_attachment(client):
    resp = client.get("/att/999999")

    assert resp.status_code == 404


def test_attachment_proxy_redirects_to_cached_url_when_not_near_expiry(client, web_conn):
    run(_seed_guild_and_channel(web_conn))
    future = datetime.now(UTC) + timedelta(hours=1)
    run(
        _seed_attachment(
            web_conn, attachment_id=1, message_id=1000, channel_id=10, url_expires_at=future
        )
    )

    resp = client.get("/att/1")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://cdn.example/cat.png"


def test_attachment_proxy_refreshes_when_near_expiry(client, web_conn, monkeypatch):
    run(_seed_guild_and_channel(web_conn))
    soon = datetime.now(UTC) + timedelta(minutes=1)
    run(
        _seed_attachment(
            web_conn, attachment_id=1, message_id=1000, channel_id=10, url_expires_at=soon
        )
    )

    async def fake_refresh(token, urls_to_refresh, **kwargs):
        assert urls_to_refresh == ["https://cdn.example/cat.png"]
        return {"https://cdn.example/cat.png": "https://cdn.example/cat.png?ex=ffffffff&is=0&hm=x&"}

    monkeypatch.setattr(attachments_view, "refresh_attachment_urls", fake_refresh)

    resp = client.get("/att/1")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://cdn.example/cat.png?ex=ffffffff&is=0&hm=x&"

    async def _fetch():
        async with web_conn.cursor() as cur:
            await cur.execute("SELECT cached_url FROM attachments WHERE id = 1")
            return await cur.fetchone()

    row = run(_fetch())
    assert row["cached_url"] == "https://cdn.example/cat.png?ex=ffffffff&is=0&hm=x&"


def test_attachment_proxy_returns_404_when_refresh_fails(client, web_conn, monkeypatch):
    run(_seed_guild_and_channel(web_conn))
    soon = datetime.now(UTC) + timedelta(minutes=1)
    run(
        _seed_attachment(
            web_conn, attachment_id=1, message_id=1000, channel_id=10, url_expires_at=soon
        )
    )

    async def fake_refresh(token, urls_to_refresh, **kwargs):
        from threadbare.web.discord_rest import AttachmentRefreshError

        raise AttachmentRefreshError("boom")

    monkeypatch.setattr(attachments_view, "refresh_attachment_urls", fake_refresh)

    resp = client.get("/att/1")

    assert resp.status_code == 404


def test_attachment_proxy_returns_404_for_an_enrolled_channel_the_requester_cannot_see(
    client, web_conn
):
    """Unlike the /jump* routes, this one hands back real file content -- so it
    has to honour the same visibility rule the listings and search use, not
    merely the id being unguessable.
    """
    run(_seed_guild_and_channel(web_conn, channel_id=10, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    future = datetime.now(UTC) + timedelta(hours=1)
    run(
        _seed_attachment(
            web_conn, attachment_id=1, message_id=1000, channel_id=10, url_expires_at=future
        )
    )

    resp = client.get("/att/1")

    assert resp.status_code == 404


def test_attachment_proxy_serves_an_enrolled_channel_the_requester_can_see(client, web_conn):
    run(_seed_guild_and_channel(web_conn, channel_id=10, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1, permissions=VIEW_CHANNEL | READ_MESSAGE_HISTORY))
    future = datetime.now(UTC) + timedelta(hours=1)
    run(
        _seed_attachment(
            web_conn, attachment_id=1, message_id=1000, channel_id=10, url_expires_at=future
        )
    )

    resp = client.get("/att/1")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://cdn.example/cat.png"


def test_attachment_proxy_returns_404_for_an_unindexed_channel(client, web_conn):
    """Matches _visibility_clause rather than channel_passes_visibility_gate:
    un-indexing a channel doesn't purge its rows, and its messages already
    vanish from every listing and from search, so its attachments must go too.
    """
    run(_seed_guild_and_channel(web_conn, channel_id=10, is_public=True, indexed=False))
    future = datetime.now(UTC) + timedelta(hours=1)
    run(
        _seed_attachment(
            web_conn, attachment_id=1, message_id=1000, channel_id=10, url_expires_at=future
        )
    )

    resp = client.get("/att/1")

    assert resp.status_code == 404


def test_attachment_proxy_resolves_visibility_through_a_thread_parent(client, web_conn):
    """A thread message's attachment keys off the parent channel, since threads
    hold no overwrites of their own.
    """
    run(_seed_guild_and_channel(web_conn, channel_id=10, is_public=False, visibility_enrolled=True))
    run(_seed_role(web_conn, role_id=1))  # @everyone, no permissions
    run(_seed_thread(web_conn, thread_id=3000, parent_channel_id=10))
    future = datetime.now(UTC) + timedelta(hours=1)
    run(
        _seed_attachment(
            web_conn, attachment_id=1, message_id=1000, thread_id=3000, url_expires_at=future
        )
    )

    resp = client.get("/att/1")

    assert resp.status_code == 404
