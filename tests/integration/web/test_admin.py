from datetime import UTC, datetime, timedelta

import threadbare

from .conftest import run


async def _seed_guild(conn, *, guild_id=1):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (guild_id, "Test Guild"),
    )


async def _seed_board(
    conn, *, channel_id, guild_id=1, name="general", is_public=True, indexed=True
):
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, 0, %s, %s, %s)
        """,
        (channel_id, guild_id, name, is_public, indexed),
    )


async def _seed_channel(conn, *, channel_id, guild_id=1, name, type, is_public=True, indexed=True):
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (channel_id, guild_id, type, name, is_public, indexed),
    )


async def _seed_heartbeat(conn, *, updated_at, last_gateway_event_at=None):
    await conn.execute(
        """
        INSERT INTO worker_heartbeat (id, updated_at, last_gateway_event_at)
        VALUES (true, %s, %s)
        """,
        (updated_at, last_gateway_event_at),
    )


def _make_mod(client):
    with client.session_transaction() as sess:
        sess["is_mod"] = True


def test_admin_index_requires_mod_session_returns_403_for_non_mod(client):
    resp = client.get("/admin/")

    assert resp.status_code == 403


def test_admin_index_lists_channels_with_indexed_and_public_state(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general", is_public=True, indexed=False))
    _make_mod(client)

    resp = client.get("/admin/")

    assert resp.status_code == 200
    body = resp.data.decode()
    assert "general" in body


def test_admin_index_excludes_voice_and_stage_voice_channels(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general"))
    run(_seed_channel(web_conn, channel_id=20, name="a-voice-channel", type=2))
    run(_seed_channel(web_conn, channel_id=21, name="a-stage", type=13))
    _make_mod(client)

    resp = client.get("/admin/")

    assert resp.status_code == 200
    body = resp.data.decode()
    assert "general" in body
    assert "a-voice-channel" not in body
    assert "a-stage" not in body


def test_toggle_indexed_flips_the_flag_and_redirects(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general", indexed=True))
    _make_mod(client)

    resp = client.post("/admin/channels/10/toggle-indexed")

    assert resp.status_code == 302

    async def _fetch():
        async with web_conn.cursor() as cur:
            await cur.execute("SELECT indexed FROM channels WHERE id = 10")
            return await cur.fetchone()

    row = run(_fetch())
    assert row["indexed"] is False


def test_toggle_indexed_returns_404_for_unknown_channel(client):
    _make_mod(client)

    resp = client.post("/admin/channels/999999/toggle-indexed")

    assert resp.status_code == 404


def test_admin_shows_stale_heartbeat_warning_when_heartbeat_is_old(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_heartbeat(web_conn, updated_at=datetime.now(UTC) - timedelta(minutes=30)))
    _make_mod(client)

    resp = client.get("/admin/")

    assert b"sync-health-stale" in resp.data


def test_admin_shows_healthy_status_when_heartbeat_is_recent(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_heartbeat(web_conn, updated_at=datetime.now(UTC) - timedelta(seconds=10)))
    _make_mod(client)

    resp = client.get("/admin/")

    assert b"sync-health-healthy" in resp.data


def test_admin_index_shows_app_version_and_latest_schema_migration(client, web_conn):
    run(_seed_guild(web_conn))
    _make_mod(client)

    resp = client.get("/admin/")

    body = resp.data.decode()
    assert threadbare.__version__ in body
    # The real test DB has every real migration applied (see
    # tests/integration/db/test_migrate.py's idempotency test) --
    # 0008_user_roles is the current latest by filename ordering.
    assert "0008_user_roles" in body


def test_admin_does_not_render_a_rebackfill_trigger_control(client, web_conn):
    # The backfill *status* column (read-only sync health) is expected and
    # fine -- what must NOT exist is any control that triggers a new
    # backfill, since that plumbing is explicitly deferred (ROADMAP.md §6).
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general"))
    _make_mod(client)

    resp = client.get("/admin/")

    body = resp.data.decode().lower()
    assert "trigger" not in body
    assert "re-backfill" not in body
    assert "rebackfill" not in body
