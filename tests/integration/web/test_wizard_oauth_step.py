from urllib.parse import parse_qs, urlparse

from threadbare.web.views import wizard as wizard_view

from .conftest import run, stub_oauth_functions

GUILD_ID = 999


async def _seed(web_conn, **fields):
    await wizard_view.wizard_queries.get_or_create_wizard_state(web_conn)
    await wizard_view.wizard_queries.update_wizard_state(web_conn, **fields)


def _seed_oauth_step(client, web_conn):
    run(
        _seed(
            web_conn,
            step="oauth",
            discord_client_id="cid",
            discord_guild_id=GUILD_ID,
            channels_confirmed=True,
        )
    )
    with client.session_transaction() as sess:
        sess["bot_token"] = "tok123"


def test_oauth_get_shows_redirect_uri(wizard_client, web_conn):
    _seed_oauth_step(wizard_client, web_conn)

    resp = wizard_client.get("/oauth")

    assert resp.status_code == 200
    assert b"/oauth/callback" in resp.data


def test_oauth_get_shows_secret_form_when_no_secret_saved_yet(wizard_client, web_conn):
    _seed_oauth_step(wizard_client, web_conn)

    resp = wizard_client.get("/oauth")

    assert b'name="client_secret"' in resp.data
    assert b"Change it" not in resp.data


def test_oauth_get_hides_secret_form_after_secret_saved(wizard_client, web_conn):
    _seed_oauth_step(wizard_client, web_conn)
    wizard_client.post("/oauth", data={"client_secret": "shh"})

    resp = wizard_client.get("/oauth")

    assert b'name="client_secret"' not in resp.data
    assert b"Change it" in resp.data
    # unaffected by this change -- still shown once the redirect URI is saved
    assert b"Test login" in resp.data


def test_oauth_get_with_edit_param_shows_secret_form_again(wizard_client, web_conn):
    _seed_oauth_step(wizard_client, web_conn)
    wizard_client.post("/oauth", data={"client_secret": "shh"})

    resp = wizard_client.get("/oauth?edit=1")

    assert b'name="client_secret"' in resp.data


def test_oauth_post_success_shows_saved_state_not_the_form(wizard_client, web_conn):
    _seed_oauth_step(wizard_client, web_conn)

    resp = wizard_client.post("/oauth", data={"client_secret": "shh"})

    assert b'name="client_secret"' not in resp.data
    assert b"Change it" in resp.data


def test_oauth_post_saves_client_secret_to_session_only(wizard_client, web_conn):
    _seed_oauth_step(wizard_client, web_conn)

    resp = wizard_client.post("/oauth", data={"client_secret": "shh"})

    assert resp.status_code == 200
    with wizard_client.session_transaction() as sess:
        assert sess["client_secret"] == "shh"


def test_oauth_test_login_redirects_to_discord_authorize_url(wizard_client, web_conn):
    _seed_oauth_step(wizard_client, web_conn)

    resp = wizard_client.get("/oauth/test-login")

    assert resp.status_code == 302
    location = urlparse(resp.headers["Location"])
    assert location.netloc == "discord.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["cid"]
    assert query["scope"] == ["identify guilds"]


def test_oauth_callback_succeeds_and_sets_verified_flag(wizard_client, web_conn, monkeypatch):
    _seed_oauth_step(wizard_client, web_conn)
    wizard_client.post("/oauth", data={"client_secret": "shh"})
    stub_oauth_functions(
        monkeypatch,
        wizard_view,
        user={"id": "1", "username": "mod"},
        guilds=[{"id": str(GUILD_ID)}],
    )

    resp = wizard_client.get("/oauth/callback?code=abc123")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/oauth"
    with wizard_client.session_transaction() as sess:
        assert sess["oauth_verified"] is True


def test_oauth_callback_with_no_code_does_not_set_verified(wizard_client, web_conn):
    _seed_oauth_step(wizard_client, web_conn)
    wizard_client.post("/oauth", data={"client_secret": "shh"})

    resp = wizard_client.get("/oauth/callback")

    assert resp.status_code == 302
    with wizard_client.session_transaction() as sess:
        assert "oauth_verified" not in sess


def test_oauth_callback_failure_does_not_set_verified(wizard_client, web_conn, monkeypatch):
    from threadbare.web.discord_rest import OAuthExchangeError

    _seed_oauth_step(wizard_client, web_conn)
    wizard_client.post("/oauth", data={"client_secret": "shh"})

    async def fake_exchange(**kwargs):
        raise OAuthExchangeError("boom")

    monkeypatch.setattr(wizard_view, "exchange_oauth_code", fake_exchange)

    resp = wizard_client.get("/oauth/callback?code=abc123")

    assert resp.status_code == 302
    with wizard_client.session_transaction() as sess:
        assert "oauth_verified" not in sess
