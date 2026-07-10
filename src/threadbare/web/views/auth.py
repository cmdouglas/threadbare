"""Discord OAuth login gate (DESIGN.md §6, `identify` + `guilds` scopes):
any member of the mirrored guild may log in and read. Rejects login
entirely -- not just admin access -- for anyone who isn't a member of
`settings.discord_guild_id`, since the gate is guild-membership-based for
the whole site, not per-page.
"""

from urllib.parse import urlencode

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from threadbare.web.authz import has_mod_permissions
from threadbare.web.discord_rest import (
    OAuthExchangeError,
    exchange_oauth_code,
    get_current_user,
    get_current_user_guilds,
)

bp = Blueprint("auth", __name__)

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
OAUTH_SCOPES = "identify guilds"


@bp.route("/login")
async def login():
    settings = current_app.config["SETTINGS"]
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_oauth_redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
    }
    return redirect(f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}")


@bp.route("/oauth/callback")
async def oauth_callback():
    settings = current_app.config["SETTINGS"]
    code = request.args.get("code")
    if not code:
        return render_template("login_denied.html"), 403

    try:
        tokens = await exchange_oauth_code(
            client_id=settings.discord_client_id,
            client_secret=settings.discord_client_secret,
            redirect_uri=settings.discord_oauth_redirect_uri,
            code=code,
        )
        access_token = tokens["access_token"]
        user = await get_current_user(access_token)
        guilds = await get_current_user_guilds(access_token)
    except (OAuthExchangeError, KeyError):
        return render_template("login_denied.html"), 403

    guild = next(
        (g for g in guilds if str(g.get("id")) == str(settings.discord_guild_id)), None
    )
    if guild is None:
        # Not a member of the mirrored guild -- reject the login entirely,
        # never populate the session (DESIGN.md §6: membership is the only
        # access check, so non-membership means no access at all).
        return render_template("login_denied.html"), 403

    session["user_id"] = int(user["id"])
    session["display_name"] = user.get("username", "")
    session["is_mod"] = has_mod_permissions(int(guild.get("permissions", 0)))

    return redirect(url_for("board_index.board_index"))


@bp.route("/logout")
async def logout():
    session.clear()
    return redirect(url_for("auth.login"))
