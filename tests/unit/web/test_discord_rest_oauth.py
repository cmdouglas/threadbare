import httpx
import pytest

from threadbare.web.discord_rest import (
    OAuthExchangeError,
    exchange_oauth_code,
    get_current_user,
    get_current_user_guilds,
)


async def test_exchange_oauth_code_posts_correct_grant_type_and_returns_tokens():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v10/oauth2/token"
        body = request.read().decode()
        assert "grant_type=authorization_code" in body
        assert "code=abc123" in body
        assert "client_id=my-client-id" in body
        assert "client_secret=my-client-secret" in body
        assert "redirect_uri=" in body
        return httpx.Response(200, json={"access_token": "tok123", "token_type": "Bearer"})

    transport = httpx.MockTransport(handler)

    result = await exchange_oauth_code(
        client_id="my-client-id",
        client_secret="my-client-secret",
        redirect_uri="http://localhost:5000/oauth/callback",
        code="abc123",
        transport=transport,
    )

    assert result == {"access_token": "tok123", "token_type": "Bearer"}


async def test_exchange_oauth_code_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "bad"}))

    with pytest.raises(OAuthExchangeError):
        await exchange_oauth_code(
            client_id="my-client-id",
            client_secret="my-client-secret",
            redirect_uri="http://localhost:5000/oauth/callback",
            code="bad-code",
            transport=transport,
        )


async def test_exchange_oauth_code_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)

    with pytest.raises(OAuthExchangeError):
        await exchange_oauth_code(
            client_id="my-client-id",
            client_secret="my-client-secret",
            redirect_uri="http://localhost:5000/oauth/callback",
            code="abc123",
            transport=transport,
        )


async def test_get_current_user_returns_parsed_user_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok123"
        assert request.url.path == "/api/v10/users/@me"
        return httpx.Response(200, json={"id": "42", "username": "alice"})

    transport = httpx.MockTransport(handler)

    result = await get_current_user("tok123", transport=transport)

    assert result == {"id": "42", "username": "alice"}


async def test_get_current_user_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={}))

    with pytest.raises(OAuthExchangeError):
        await get_current_user("bad-token", transport=transport)


async def test_get_current_user_guilds_returns_list_including_permissions_field():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok123"
        assert request.url.path == "/api/v10/users/@me/guilds"
        return httpx.Response(200, json=[{"id": "999", "name": "Test Guild", "permissions": "40"}])

    transport = httpx.MockTransport(handler)

    result = await get_current_user_guilds("tok123", transport=transport)

    assert result == [{"id": "999", "name": "Test Guild", "permissions": "40"}]


async def test_get_current_user_guilds_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)

    with pytest.raises(OAuthExchangeError):
        await get_current_user_guilds("tok123", transport=transport)
