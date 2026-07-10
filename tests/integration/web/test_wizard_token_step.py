from threadbare.web.views import wizard as wizard_view


def test_token_get_renders_form(wizard_client):
    resp = wizard_client.get("/token")

    assert resp.status_code == 200
    assert b"bot_token" in resp.data


def test_token_post_with_valid_token_advances_to_invite(wizard_client, monkeypatch):
    async def fake_get_bot_user(token, **kwargs):
        return {"id": "1", "username": "mybot"}

    monkeypatch.setattr(wizard_view, "get_bot_user", fake_get_bot_user)

    resp = wizard_client.post("/token", data={"bot_token": "tok123", "client_id": "cid"})

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/invite"
    with wizard_client.session_transaction() as sess:
        assert sess["bot_token"] == "tok123"


def test_token_post_with_invalid_token_shows_error(wizard_client, monkeypatch):
    from threadbare.web.discord_rest import BotIdentityError

    async def fake_get_bot_user(token, **kwargs):
        raise BotIdentityError("nope")

    monkeypatch.setattr(wizard_view, "get_bot_user", fake_get_bot_user)

    resp = wizard_client.post("/token", data={"bot_token": "bad", "client_id": "cid"})

    assert resp.status_code == 200
    with wizard_client.session_transaction() as sess:
        assert "bot_token" not in sess


def test_token_post_missing_fields_shows_error(wizard_client):
    resp = wizard_client.post("/token", data={"bot_token": "", "client_id": ""})

    assert resp.status_code == 200
    with wizard_client.session_transaction() as sess:
        assert "bot_token" not in sess


def test_index_redirects_to_current_step(wizard_client, web_conn):
    resp = wizard_client.get("/")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/intro"


def test_visiting_a_step_beyond_progress_bounces_back(wizard_client):
    resp = wizard_client.get("/finish")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/intro"
