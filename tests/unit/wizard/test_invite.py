from urllib.parse import parse_qs, urlparse

from threadbare.discord_permissions import REQUIRED_PERMISSIONS
from threadbare.wizard.invite import build_invite_url


def test_build_invite_url_uses_required_permissions_by_default():
    url = build_invite_url("my-client-id")

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "discord.com"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["my-client-id"]
    assert query["scope"] == ["bot"]
    assert query["permissions"] == [str(REQUIRED_PERMISSIONS)]
    assert REQUIRED_PERMISSIONS == 66560


def test_build_invite_url_accepts_a_custom_permissions_value():
    url = build_invite_url("my-client-id", permissions=8)

    query = parse_qs(urlparse(url).query)
    assert query["permissions"] == ["8"]
