from datetime import UTC, datetime

import httpx
import pytest

from threadbare.web.discord_rest import (
    AttachmentRefreshError,
    SignedUrlExpiryError,
    parse_expiry_from_signed_url,
    refresh_attachment_urls,
)

SAMPLE_URL = (
    "https://cdn.discordapp.com/attachments/111/222/cat.png"
    "?ex=66b2a400&is=66b15280&hm=abcdef1234567890abcdef1234567890abcdef1234567890abcdef12345678&"
)


def test_parse_expiry_from_signed_url_parses_the_hex_timestamp():
    expiry = parse_expiry_from_signed_url(SAMPLE_URL)

    assert expiry == datetime(2024, 8, 6, 22, 30, 24, tzinfo=UTC)


def test_parse_expiry_from_signed_url_raises_when_ex_param_is_missing():
    url = "https://cdn.discordapp.com/attachments/111/222/cat.png?is=66b15280&hm=abc&"

    with pytest.raises(SignedUrlExpiryError):
        parse_expiry_from_signed_url(url)


def test_parse_expiry_from_signed_url_raises_for_a_non_hex_ex_param():
    url = "https://cdn.discordapp.com/attachments/111/222/cat.png?ex=not-hex&"

    with pytest.raises(SignedUrlExpiryError):
        parse_expiry_from_signed_url(url)


async def test_refresh_attachment_urls_returns_original_to_refreshed_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bot tok"
        assert request.url.path == "/api/v10/attachments/refresh-urls"
        return httpx.Response(
            200,
            json={
                "refreshed_urls": [
                    {
                        "original": "https://cdn.discordapp.com/a/old",
                        "refreshed": "https://cdn.discordapp.com/a/new",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)

    result = await refresh_attachment_urls(
        "tok", ["https://cdn.discordapp.com/a/old"], transport=transport
    )

    assert result == {"https://cdn.discordapp.com/a/old": "https://cdn.discordapp.com/a/new"}


async def test_refresh_attachment_urls_raises_on_non_2xx_response():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={}))

    with pytest.raises(AttachmentRefreshError):
        await refresh_attachment_urls(
            "tok", ["https://cdn.discordapp.com/a/old"], transport=transport
        )


async def test_refresh_attachment_urls_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    transport = httpx.MockTransport(handler)

    with pytest.raises(AttachmentRefreshError):
        await refresh_attachment_urls(
            "tok", ["https://cdn.discordapp.com/a/old"], transport=transport
        )


async def test_refresh_attachment_urls_raises_on_unexpected_response_shape():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"unexpected": True}))

    with pytest.raises(AttachmentRefreshError):
        await refresh_attachment_urls(
            "tok", ["https://cdn.discordapp.com/a/old"], transport=transport
        )
