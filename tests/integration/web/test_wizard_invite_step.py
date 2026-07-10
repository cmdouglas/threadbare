from threadbare.web.views import wizard as wizard_view

from .conftest import run


async def _seed(web_conn, **fields):
    await wizard_view.wizard_queries.get_or_create_wizard_state(web_conn)
    await wizard_view.wizard_queries.update_wizard_state(web_conn, **fields)


def _seed_token_step(client, web_conn):
    run(_seed(web_conn, step="invite", discord_client_id="cid"))
    with client.session_transaction() as sess:
        sess["bot_token"] = "tok123"


def test_invite_get_shows_invite_url(wizard_client, web_conn):
    _seed_token_step(wizard_client, web_conn)

    resp = wizard_client.get("/invite")

    assert resp.status_code == 200
    assert b"discord.com" in resp.data


def test_invite_check_now_auto_advances_for_a_single_guild(wizard_client, web_conn, monkeypatch):
    _seed_token_step(wizard_client, web_conn)

    async def fake_get_bot_guilds(token, **kwargs):
        return [{"id": "999", "name": "Test Guild"}]

    monkeypatch.setattr(wizard_view, "get_bot_guilds", fake_get_bot_guilds)

    resp = wizard_client.post("/invite", data={})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/channels"


def test_invite_check_now_shows_picker_for_multiple_guilds(wizard_client, web_conn, monkeypatch):
    _seed_token_step(wizard_client, web_conn)

    async def fake_get_bot_guilds(token, **kwargs):
        return [{"id": "999", "name": "Guild A"}, {"id": "888", "name": "Guild B"}]

    monkeypatch.setattr(wizard_view, "get_bot_guilds", fake_get_bot_guilds)

    resp = wizard_client.post("/invite", data={})

    assert resp.status_code == 200
    assert b"Guild A" in resp.data
    assert b"Guild B" in resp.data


def test_invite_check_now_shows_error_when_bot_has_joined_nothing(
    wizard_client, web_conn, monkeypatch
):
    _seed_token_step(wizard_client, web_conn)

    async def fake_get_bot_guilds(token, **kwargs):
        return []

    monkeypatch.setattr(wizard_view, "get_bot_guilds", fake_get_bot_guilds)

    resp = wizard_client.post("/invite", data={})

    assert resp.status_code == 200
    assert b"hasn" in resp.data.lower()


def test_invite_picker_selection_advances_to_channels(wizard_client, web_conn):
    _seed_token_step(wizard_client, web_conn)

    resp = wizard_client.post("/invite", data={"guild_id": "999"})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/channels"
