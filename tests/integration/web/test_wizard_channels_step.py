from threadbare.web.views import wizard as wizard_view

from .conftest import run

GUILD_ID = 999
CHANNEL_ID = 111
CATEGORY_ID = 222
BOT_USER_ID = 1


async def _seed(web_conn, **fields):
    await wizard_view.wizard_queries.get_or_create_wizard_state(web_conn)
    await wizard_view.wizard_queries.update_wizard_state(web_conn, **fields)


def _seed_invite_step(client, web_conn, *, channels_confirmed=False):
    run(
        _seed(
            web_conn,
            step="channels",
            discord_client_id="cid",
            discord_guild_id=GUILD_ID,
            channels_confirmed=channels_confirmed,
        )
    )
    with client.session_transaction() as sess:
        sess["bot_token"] = "tok123"


def _stub_discord(monkeypatch, *, everyone_perms=1024 | 65536, overwrites=None, in_category=False):
    overwrites = overwrites if overwrites is not None else []

    async def fake_get_guild_channels(token, guild_id, **kwargs):
        channels = [
            {
                "id": str(CHANNEL_ID),
                "type": 0,
                "name": "general",
                "position": 0,
                "parent_id": str(CATEGORY_ID) if in_category else None,
                "topic": None,
                "permission_overwrites": overwrites,
            }
        ]
        if in_category:
            channels.append(
                {
                    "id": str(CATEGORY_ID),
                    "type": 4,
                    "name": "Text Channels",
                    "position": 0,
                    "parent_id": None,
                    "topic": None,
                    "permission_overwrites": [],
                }
            )
        return channels

    async def fake_get_guild_roles(token, guild_id, **kwargs):
        return [{"id": str(guild_id), "permissions": str(everyone_perms)}]

    async def fake_get_bot_user(token, **kwargs):
        return {"id": str(BOT_USER_ID), "username": "mybot"}

    async def fake_get_guild_member(token, guild_id, user_id, **kwargs):
        return {"user": {"id": str(BOT_USER_ID)}, "roles": []}

    async def fake_get_recent_channel_message(token, channel_id, **kwargs):
        return {"id": "1", "content": "hi"}

    monkeypatch.setattr(wizard_view, "get_guild_channels", fake_get_guild_channels)
    monkeypatch.setattr(wizard_view, "get_guild_roles", fake_get_guild_roles)
    monkeypatch.setattr(wizard_view, "get_bot_user", fake_get_bot_user)
    monkeypatch.setattr(wizard_view, "get_guild_member", fake_get_guild_member)
    monkeypatch.setattr(wizard_view, "get_recent_channel_message", fake_get_recent_channel_message)


def test_channels_get_shows_discovered_channel(wizard_client, web_conn, monkeypatch):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch)

    resp = wizard_client.get("/channels")

    assert resp.status_code == 200
    assert b"general" in resp.data
    assert b"Message Content intent: enabled" in resp.data


def test_channels_get_shows_denied_when_bot_lacks_permission(wizard_client, web_conn, monkeypatch):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch, everyone_perms=0)

    resp = wizard_client.get("/channels")

    assert resp.status_code == 200
    assert b"denied" in resp.data


def test_channels_post_confirms_selection_and_advances(wizard_client, web_conn, monkeypatch):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch)
    wizard_client.get("/channels")

    resp = wizard_client.post("/channels", data={"indexed_channel_id": [str(CHANNEL_ID)]})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/oauth"

    async def _fetch():
        return await wizard_view.wizard_queries.get_channels_for_guild(web_conn, GUILD_ID)

    channels = run(_fetch())
    assert channels[0]["indexed"] is True


def test_channels_get_succeeds_when_channel_is_inside_a_category(
    wizard_client, web_conn, monkeypatch
):
    # Regression test for a real bug found via live testing against an
    # actual Discord guild: category rows were never seeded, so a child
    # channel's parent_id FK violated (channels_parent_id_fkey) the moment
    # any category existed at all -- the sync worker's own discover_channels
    # hit and fixed this identical ordering issue previously.
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch, in_category=True)

    resp = wizard_client.get("/channels")

    assert resp.status_code == 200
    assert b"general" in resp.data
    assert b"Text Channels" not in resp.data  # the category itself isn't a selectable row


def test_channels_get_excludes_voice_and_stage_voice_channels(wizard_client, web_conn, monkeypatch):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch)

    async def fake_get_guild_channels_with_voice(token, guild_id, **kwargs):
        return [
            {
                "id": str(CHANNEL_ID),
                "type": 0,
                "name": "general",
                "position": 0,
                "parent_id": None,
                "topic": None,
                "permission_overwrites": [],
            },
            {
                "id": "333",
                "type": 2,
                "name": "a-voice-channel",
                "position": 1,
                "parent_id": None,
                "topic": None,
                "permission_overwrites": [],
            },
            {
                "id": "334",
                "type": 13,
                "name": "a-stage",
                "position": 2,
                "parent_id": None,
                "topic": None,
                "permission_overwrites": [],
            },
        ]

    monkeypatch.setattr(wizard_view, "get_guild_channels", fake_get_guild_channels_with_voice)

    resp = wizard_client.get("/channels")

    assert resp.status_code == 200
    assert b"general" in resp.data
    assert b"a-voice-channel" not in resp.data
    assert b"a-stage" not in resp.data


def test_channels_revisit_preserves_prior_confirmation(wizard_client, web_conn, monkeypatch):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch)
    wizard_client.get("/channels")
    wizard_client.post("/channels", data={"indexed_channel_id": [str(CHANNEL_ID)]})

    # Revisit: channels_confirmed is now True in wizard_state, so re-fetching
    # the (possibly renamed) live channel list must not wipe the confirmation.
    resp = wizard_client.get("/channels")

    assert resp.status_code == 200
    assert b"checked" in resp.data


def test_channels_get_shows_auto_index_checked_by_default(wizard_client, web_conn, monkeypatch):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch)

    resp = wizard_client.get("/channels")

    assert resp.status_code == 200
    assert b'name="auto_index_new_channels"' in resp.data
    assert b'name="auto_index_new_channels" checked' in resp.data


def test_channels_post_without_the_checkbox_disables_auto_index(
    wizard_client, web_conn, monkeypatch
):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch)
    wizard_client.get("/channels")

    resp = wizard_client.post("/channels", data={"indexed_channel_id": [str(CHANNEL_ID)]})

    assert resp.status_code == 302

    async def _fetch():
        return await wizard_view.wizard_queries.get_auto_index_new_channels(web_conn)

    assert run(_fetch()) is False


def test_channels_post_with_the_checkbox_keeps_auto_index_enabled(
    wizard_client, web_conn, monkeypatch
):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch)
    wizard_client.get("/channels")

    resp = wizard_client.post(
        "/channels",
        data={
            "indexed_channel_id": [str(CHANNEL_ID)],
            "auto_index_new_channels": "on",
        },
    )

    assert resp.status_code == 302

    async def _fetch():
        return await wizard_view.wizard_queries.get_auto_index_new_channels(web_conn)

    assert run(_fetch()) is True


def test_channels_revisit_reflects_the_previously_saved_auto_index_value(
    wizard_client, web_conn, monkeypatch
):
    _seed_invite_step(wizard_client, web_conn)
    _stub_discord(monkeypatch)
    wizard_client.get("/channels")
    wizard_client.post("/channels", data={"indexed_channel_id": [str(CHANNEL_ID)]})

    resp = wizard_client.get("/channels")

    assert resp.status_code == 200
    assert b'name="auto_index_new_channels" checked' not in resp.data
