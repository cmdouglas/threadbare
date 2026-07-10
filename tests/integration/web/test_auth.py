from urllib.parse import parse_qs, urlparse

from threadbare.web.views import auth as auth_view


def _stub_oauth(monkeypatch, *, user, guilds):
    async def fake_exchange(**kwargs):
        return {"access_token": "tok123"}

    async def fake_get_user(token, **kwargs):
        return user

    async def fake_get_guilds(token, **kwargs):
        return guilds

    monkeypatch.setattr(auth_view, "exchange_oauth_code", fake_exchange)
    monkeypatch.setattr(auth_view, "get_current_user", fake_get_user)
    monkeypatch.setattr(auth_view, "get_current_user_guilds", fake_get_guilds)


def test_login_redirects_to_discord_authorize_url_with_correct_params(anonymous_client):
    resp = anonymous_client.get("/login")

    assert resp.status_code == 302
    location = urlparse(resp.headers["Location"])
    assert location.netloc == "discord.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["test-client-id"]
    assert query["redirect_uri"] == ["http://localhost:5000/oauth/callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["identify guilds"]


def test_oauth_callback_sets_session_and_redirects_for_a_guild_member(
    anonymous_client, monkeypatch
):
    _stub_oauth(
        monkeypatch,
        user={"id": "42", "username": "alice"},
        guilds=[{"id": "1", "name": "Test Guild", "permissions": "0"}],
    )

    resp = anonymous_client.get("/oauth/callback?code=abc123")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"
    with anonymous_client.session_transaction() as sess:
        assert sess["user_id"] == 42
        assert sess["display_name"] == "alice"
        assert sess["is_mod"] is False


def test_oauth_callback_sets_is_mod_true_when_permissions_include_manage_guild(
    anonymous_client, monkeypatch
):
    _stub_oauth(
        monkeypatch,
        user={"id": "42", "username": "alice"},
        guilds=[{"id": "1", "name": "Test Guild", "permissions": str(1 << 5)}],
    )

    anonymous_client.get("/oauth/callback?code=abc123")

    with anonymous_client.session_transaction() as sess:
        assert sess["is_mod"] is True


def test_oauth_callback_rejects_login_entirely_for_non_member(anonymous_client, monkeypatch):
    _stub_oauth(
        monkeypatch,
        user={"id": "42", "username": "alice"},
        guilds=[{"id": "999999", "name": "Some Other Guild", "permissions": "0"}],
    )

    resp = anonymous_client.get("/oauth/callback?code=abc123")

    assert resp.status_code == 403
    with anonymous_client.session_transaction() as sess:
        assert "user_id" not in sess


def test_oauth_callback_with_no_code_is_denied(anonymous_client):
    resp = anonymous_client.get("/oauth/callback")

    assert resp.status_code == 403


def test_logout_clears_session(client):
    resp = client.get("/logout")

    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_any_page_redirects_to_login_when_session_has_no_user_id(anonymous_client):
    resp = anonymous_client.get("/")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"


def test_logged_in_member_can_reach_board_index(client):
    resp = client.get("/")

    assert resp.status_code == 200
