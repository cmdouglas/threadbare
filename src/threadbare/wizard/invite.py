"""Bot invite URL builder for the setup wizard's /invite step. No
guild_id/disable_guild_select param: the wizard doesn't know which guild
the mod will pick yet -- they choose it in Discord's own consent screen,
and the wizard discovers the result afterward via a bot-token
`GET /users/@me/guilds` call (web/discord_rest.py's get_bot_guilds).
"""

from urllib.parse import urlencode

from threadbare.discord_permissions import REQUIRED_PERMISSIONS

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"


def build_invite_url(client_id: str, *, permissions: int = REQUIRED_PERMISSIONS) -> str:
    params = {
        "client_id": client_id,
        "scope": "bot",
        "permissions": str(permissions),
    }
    return f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"
