import httpx
import pytest

from threadbare.web.discord_rest import (
    BotIdentityError,
    ChannelMessageFetchError,
    GuildFetchError,
    get_bot_guilds,
    get_bot_user,
    get_guild,
    get_guild_channels,
    get_guild_member,
    get_guild_roles,
    get_recent_channel_message,
)


def _assert_bot_auth(request: httpx.Request, token: str = "tok123") -> None:
    assert request.headers["authorization"] == f"Bot {token}"


async def test_get_bot_user_returns_parsed_user_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_bot_auth(request)
        assert request.url.path == "/api/v10/users/@me"
        return httpx.Response(200, json={"id": "1", "username": "mybot"})

    result = await get_bot_user("tok123", transport=httpx.MockTransport(handler))
    assert result == {"id": "1", "username": "mybot"}


async def test_get_bot_user_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={}))
    with pytest.raises(BotIdentityError):
        await get_bot_user("bad-token", transport=transport)


async def test_get_bot_user_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(BotIdentityError):
        await get_bot_user("tok123", transport=httpx.MockTransport(handler))


async def test_get_bot_guilds_returns_list():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_bot_auth(request)
        assert request.url.path == "/api/v10/users/@me/guilds"
        return httpx.Response(200, json=[{"id": "999", "name": "Test Guild"}])

    result = await get_bot_guilds("tok123", transport=httpx.MockTransport(handler))
    assert result == [{"id": "999", "name": "Test Guild"}]


async def test_get_bot_guilds_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={}))
    with pytest.raises(BotIdentityError):
        await get_bot_guilds("tok123", transport=transport)


async def test_get_bot_guilds_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(BotIdentityError):
        await get_bot_guilds("tok123", transport=httpx.MockTransport(handler))


async def test_get_guild_returns_parsed_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_bot_auth(request)
        assert request.url.path == "/api/v10/guilds/999"
        return httpx.Response(200, json={"id": "999", "roles": []})

    result = await get_guild("tok123", 999, transport=httpx.MockTransport(handler))
    assert result == {"id": "999", "roles": []}


async def test_get_guild_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={}))
    with pytest.raises(GuildFetchError):
        await get_guild("tok123", 999, transport=transport)


async def test_get_guild_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(GuildFetchError):
        await get_guild("tok123", 999, transport=httpx.MockTransport(handler))


async def test_get_guild_channels_returns_list():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_bot_auth(request)
        assert request.url.path == "/api/v10/guilds/999/channels"
        return httpx.Response(200, json=[{"id": "1", "type": 0}])

    result = await get_guild_channels("tok123", 999, transport=httpx.MockTransport(handler))
    assert result == [{"id": "1", "type": 0}]


async def test_get_guild_channels_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    with pytest.raises(GuildFetchError):
        await get_guild_channels("tok123", 999, transport=transport)


async def test_get_guild_channels_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(GuildFetchError):
        await get_guild_channels("tok123", 999, transport=httpx.MockTransport(handler))


async def test_get_guild_roles_returns_list():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_bot_auth(request)
        assert request.url.path == "/api/v10/guilds/999/roles"
        return httpx.Response(200, json=[{"id": "1", "permissions": "0"}])

    result = await get_guild_roles("tok123", 999, transport=httpx.MockTransport(handler))
    assert result == [{"id": "1", "permissions": "0"}]


async def test_get_guild_roles_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    with pytest.raises(GuildFetchError):
        await get_guild_roles("tok123", 999, transport=transport)


async def test_get_guild_roles_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(GuildFetchError):
        await get_guild_roles("tok123", 999, transport=httpx.MockTransport(handler))


async def test_get_guild_member_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_bot_auth(request)
        assert request.url.path == "/api/v10/guilds/999/members/1"
        return httpx.Response(200, json={"user": {"id": "1"}, "roles": ["42"]})

    result = await get_guild_member("tok123", 999, 1, transport=httpx.MockTransport(handler))
    assert result == {"user": {"id": "1"}, "roles": ["42"]}


async def test_get_guild_member_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={}))
    with pytest.raises(GuildFetchError):
        await get_guild_member("tok123", 999, 1, transport=transport)


async def test_get_guild_member_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(GuildFetchError):
        await get_guild_member("tok123", 999, 1, transport=httpx.MockTransport(handler))


async def test_get_recent_channel_message_returns_first_message():
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_bot_auth(request)
        assert request.url.path == "/api/v10/channels/1/messages"
        assert request.url.params["limit"] == "1"
        return httpx.Response(200, json=[{"id": "1", "content": "hi"}])

    result = await get_recent_channel_message("tok123", 1, transport=httpx.MockTransport(handler))
    assert result == {"id": "1", "content": "hi"}


async def test_get_recent_channel_message_returns_none_when_empty():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    result = await get_recent_channel_message("tok123", 1, transport=transport)
    assert result is None


async def test_get_recent_channel_message_raises_on_non_2xx():
    transport = httpx.MockTransport(lambda request: httpx.Response(403, json={}))
    with pytest.raises(ChannelMessageFetchError):
        await get_recent_channel_message("tok123", 1, transport=transport)


async def test_get_recent_channel_message_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(ChannelMessageFetchError):
        await get_recent_channel_message("tok123", 1, transport=httpx.MockTransport(handler))
