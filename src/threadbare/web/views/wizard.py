"""First-run setup wizard routes (ROADMAP.md §7, DESIGN.md §8.1's steps).

Session-loss resilience: only the bot token and OAuth client secret ever
live in this app's (ephemeral) Flask session -- everything else (client
id, guild id, redirect URI, channel confirmations) persists in
db/wizard_queries.py's wizard_state row. The before_request hook below
notices when a secret has gone missing (process restart, closed tab) and
bounces the request back to whichever step re-collects it
(wizard.steps.resolve_resume_step), rather than crashing on a missing
session key or silently restarting the whole flow.

No "trigger re-backfill" and no live progress page: the wizard ends once
config is validated and the mod has confirmed indexed channels. The last
screen tells the operator to restart the sync worker themselves (ROADMAP.md
§6's admin page already established this same deferral for the same
web-app/sync-worker IPC gap).
"""

import os
import secrets as _secrets
from urllib.parse import urlencode

from dotenv import load_dotenv
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from threadbare.channel_types import CATEGORY, NON_CONTENT_TYPES
from threadbare.config import ConfigError, load_settings
from threadbare.db import wizard_queries
from threadbare.discord_permissions import REQUIRED_PERMISSIONS, compute_is_public
from threadbare.web.discord_rest import (
    DiscordRestError,
    exchange_oauth_code,
    get_bot_guilds,
    get_bot_user,
    get_current_user,
    get_current_user_guilds,
    get_guild_channels,
    get_guild_member,
    get_guild_roles,
    get_recent_channel_message,
)
from threadbare.wizard.env_file import write_env_updates
from threadbare.wizard.invite import build_invite_url
from threadbare.wizard.preflight import (
    compute_channel_permission_table,
    message_content_intent_ok,
    parse_overwrites,
)
from threadbare.wizard.steps import WIZARD_STEPS, is_step_reachable, resolve_resume_step

bp = Blueprint("wizard", __name__)

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
OAUTH_TEST_SCOPES = "identify guilds"

# Which "step" each GET page corresponds to, for the anti-skip-ahead check
# (is_step_reachable). Endpoints not listed here (index, the two OAuth
# round-trip routes, pitch_kit) aren't gated by it -- either they have their
# own inline precondition checks, or they're always safe to view.
_STEP_FOR_ENDPOINT = {
    "wizard.intro": "intro",
    "wizard.token": "token",
    "wizard.invite": "invite",
    "wizard.channels": "channels",
    "wizard.oauth": "oauth",
    "wizard.finish": "complete",
}

# oauth_callback must always be reachable -- it's registered at the exact
# path a mod pastes into Discord's developer portal as the redirect URI,
# and that has to keep working regardless of wizard progress.
_ALWAYS_ALLOWED_ENDPOINTS = {"wizard.oauth_callback", "wizard.static"}


def _route_for_step(step: str) -> str:
    return "finish" if step == "complete" else step


@bp.before_request
async def enforce_wizard_flow():
    if request.endpoint in _ALWAYS_ALLOWED_ENDPOINTS:
        return None

    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)

    resume_target = resolve_resume_step(
        wizard_step=state["step"],
        has_bot_token="bot_token" in session,
        has_client_secret="client_secret" in session,
    )
    target_endpoint = f"wizard.{_route_for_step(resume_target)}"
    if resume_target != state["step"] and request.endpoint != target_endpoint:
        return redirect(url_for(target_endpoint))

    requested_step = _STEP_FOR_ENDPOINT.get(request.endpoint or "")
    if requested_step and not is_step_reachable(requested_step, completed_step=state["step"]):
        return redirect(url_for(f"wizard.{_route_for_step(state['step'])}"))

    return None


@bp.route("/")
async def index():
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)
    return redirect(url_for(f"wizard.{_route_for_step(state['step'])}"))


@bp.route("/intro")
async def intro():
    return render_template("wizard_intro.html")


@bp.route("/token", methods=["GET", "POST"])
async def token():
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)

    if request.method == "POST":
        bot_token = request.form.get("bot_token", "").strip()
        client_id = request.form.get("client_id", "").strip()
        if not bot_token or not client_id:
            flash("Both the bot token and client ID are required.")
            return render_template("wizard_token.html", state=state)

        try:
            await get_bot_user(bot_token)
        except DiscordRestError:
            flash(
                "That token was rejected by Discord -- double check it's the Bot "
                "token, not the client secret."
            )
            return render_template("wizard_token.html", state=state)

        session["bot_token"] = bot_token
        new_step = state["step"]
        if WIZARD_STEPS.index(new_step) < WIZARD_STEPS.index("invite"):
            new_step = "invite"
        async with pool.connection() as conn:
            await wizard_queries.update_wizard_state(
                conn, step=new_step, discord_client_id=client_id
            )
        # Redirect to wherever progress has actually reached -- not always
        # straight to /invite: re-entering the token after a session-loss
        # bounce (see resolve_resume_step) should return to the real resume
        # point (e.g. /channels or /oauth), not restart the flow.
        return redirect(url_for(f"wizard.{_route_for_step(new_step)}"))

    return render_template("wizard_token.html", state=state)


@bp.route("/invite", methods=["GET", "POST"])
async def invite():
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)

    if request.method == "POST":
        selected_guild_id = request.form.get("guild_id")
        if selected_guild_id:
            async with pool.connection() as conn:
                await wizard_queries.update_wizard_state(
                    conn, step="channels", discord_guild_id=int(selected_guild_id)
                )
            return redirect(url_for("wizard.channels"))

        try:
            guilds = await get_bot_guilds(session["bot_token"])
        except DiscordRestError:
            flash("Couldn't reach Discord to check -- try again in a moment.")
            return render_template("wizard_invite.html", state=state, invite_url=_invite_url(state))

        if not guilds:
            flash("The bot hasn't joined a server yet -- use the invite link above first.")
            return render_template("wizard_invite.html", state=state, invite_url=_invite_url(state))

        if len(guilds) == 1:
            async with pool.connection() as conn:
                await wizard_queries.update_wizard_state(
                    conn, step="channels", discord_guild_id=int(guilds[0]["id"])
                )
            return redirect(url_for("wizard.channels"))

        return render_template(
            "wizard_invite.html", state=state, invite_url=_invite_url(state), guild_choices=guilds
        )

    return render_template("wizard_invite.html", state=state, invite_url=_invite_url(state))


def _invite_url(state: dict) -> str:
    return build_invite_url(state["discord_client_id"], permissions=REQUIRED_PERMISSIONS)


@bp.route("/channels", methods=["GET", "POST"])
async def channels():
    pool = current_app.config["POOL"]
    bot_token = session["bot_token"]

    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)
    guild_id = state["discord_guild_id"]

    if request.method == "POST":
        selected_ids = {int(v) for v in request.form.getlist("indexed_channel_id")}
        auto_index_new_channels = "auto_index_new_channels" in request.form
        async with pool.connection() as conn:
            await wizard_queries.confirm_channel_selection(conn, guild_id, selected_ids)
            await wizard_queries.set_auto_index_new_channels(conn, auto_index_new_channels)
            await wizard_queries.update_wizard_state(conn, step="oauth", channels_confirmed=True)
        return redirect(url_for("wizard.oauth"))

    guild_channels = await get_guild_channels(bot_token, guild_id)
    guild_roles = await get_guild_roles(bot_token, guild_id)
    bot_user = await get_bot_user(bot_token)
    bot_member = await get_guild_member(bot_token, guild_id, int(bot_user["id"]))

    everyone_role_id = guild_id  # Discord convention: @everyone's role id == the guild id
    base_permissions = 0
    for role in guild_roles:
        if str(role["id"]) == str(everyone_role_id) or role["id"] in bot_member.get("roles", []):
            base_permissions |= int(role["permissions"])
    bot_role_ids = {int(r) for r in bot_member.get("roles", [])}

    categories = {c["id"]: c for c in guild_channels if c["type"] == CATEGORY}
    category_overwrites = {
        cat_id: parse_overwrites(cat["permission_overwrites"]) for cat_id, cat in categories.items()
    }

    # Categories need their own row too -- a child channel's parent_id is a
    # foreign key into channels itself, so categories must be seeded before
    # any channel that references one (found via a live FK-violation test
    # against a real multi-category Discord guild; the sync worker's own
    # discover_channels hit and fixed this same ordering issue previously).
    category_rows = [
        {
            "id": int(cat["id"]),
            "guild_id": guild_id,
            "parent_id": None,
            "type": cat["type"],
            "name": cat["name"],
            "position": cat.get("position", 0),
            "topic": None,
        }
        for cat in categories.values()
    ]

    channel_rows = []
    for ch in guild_channels:
        if ch["type"] in NON_CONTENT_TYPES:
            continue
        parent_id = int(ch["parent_id"]) if ch.get("parent_id") else None
        channel_rows.append(
            {
                "id": int(ch["id"]),
                "guild_id": guild_id,
                "parent_id": parent_id,
                "type": ch["type"],
                "name": ch["name"],
                "position": ch.get("position", 0),
                "topic": ch.get("topic"),
                "overwrites": parse_overwrites(ch.get("permission_overwrites", [])),
            }
        )

    non_category_rows = [
        {k: v for k, v in row.items() if k != "overwrites"} for row in channel_rows
    ]
    guild_row = {"id": guild_id, "name": f"guild-{guild_id}", "icon": None}
    async with pool.connection() as conn:
        await wizard_queries.seed_guild_and_channels(
            conn,
            guild_row,
            category_rows + non_category_rows,
            already_confirmed=state["channels_confirmed"],
        )
        existing = {c["id"]: c for c in await wizard_queries.get_channels_for_guild(conn, guild_id)}
        auto_index_new_channels = await wizard_queries.get_auto_index_new_channels(conn)

    permission_table = compute_channel_permission_table(
        base_permissions=base_permissions,
        everyone_role_id=int(everyone_role_id),
        bot_role_ids=bot_role_ids,
        bot_user_id=int(bot_user["id"]),
        channels=[
            {"id": row["id"], "parent_id": row["parent_id"], "overwrites": row["overwrites"]}
            for row in channel_rows
        ],
        category_overwrites={int(k): v for k, v in category_overwrites.items()},
    )
    permission_by_id = {row["channel_id"]: row for row in permission_table}

    rows = []
    for row in channel_rows:
        cat_overwrite = category_overwrites.get(row["parent_id"], [])
        is_public = compute_is_public(
            base_permissions,
            next((o for o in cat_overwrite if o.id == guild_id), None),
            next((o for o in row["overwrites"] if o.id == guild_id), None),
        )
        rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "is_public": is_public,
                "bot_ok": permission_by_id[row["id"]]["ok"],
                "overwrite_denied": permission_by_id[row["id"]]["overwrite_denied"],
                "indexed": existing[row["id"]]["indexed"],
            }
        )

    # Message Content intent (DESIGN.md §8.2): one check is enough, using
    # any channel the bot can actually read -- not a per-channel repeat.
    intent_status = None
    readable_channel = next((r for r in rows if r["bot_ok"]), None)
    if readable_channel is not None:
        sample_message = await get_recent_channel_message(bot_token, readable_channel["id"])
        intent_status = message_content_intent_ok(sample_message)

    return render_template(
        "wizard_channels.html",
        state=state,
        channel_rows=rows,
        intent_status=intent_status,
        auto_index_new_channels=auto_index_new_channels,
    )


@bp.route("/oauth", methods=["GET", "POST"])
async def oauth():
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)

    redirect_uri = url_for("wizard.oauth_callback", _external=True)

    if request.method == "POST":
        client_secret = request.form.get("client_secret", "").strip()
        if not client_secret:
            flash("The client secret is required.")
            return render_template(
                "wizard_oauth.html",
                state=state,
                redirect_uri=redirect_uri,
                has_client_secret="client_secret" in session,
                show_secret_form=True,
            )

        session["client_secret"] = client_secret
        async with pool.connection() as conn:
            await wizard_queries.update_wizard_state(conn, discord_oauth_redirect_uri=redirect_uri)
        # state was fetched before the update above, so its
        # discord_oauth_redirect_uri is still stale (None on the very first
        # save) -- patch in the value we just persisted rather than
        # re-querying, since it's the exact value we just wrote.
        state = {**state, "discord_oauth_redirect_uri": redirect_uri}
        flash('Saved. Click "Test login" below to verify the round trip.')
        return render_template(
            "wizard_oauth.html",
            state=state,
            redirect_uri=redirect_uri,
            has_client_secret=True,
            show_secret_form=False,
        )

    has_client_secret = "client_secret" in session
    return render_template(
        "wizard_oauth.html",
        state=state,
        redirect_uri=redirect_uri,
        oauth_verified=session.get("oauth_verified", False),
        has_client_secret=has_client_secret,
        show_secret_form=request.args.get("edit") == "1" or not has_client_secret,
    )


@bp.route("/oauth/test-login")
async def oauth_test_login():
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)

    params = {
        "client_id": state["discord_client_id"],
        "redirect_uri": url_for("wizard.oauth_callback", _external=True),
        "response_type": "code",
        "scope": OAUTH_TEST_SCOPES,
    }
    return redirect(f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}")


@bp.route("/oauth/callback")
async def oauth_callback():
    code = request.args.get("code")
    if not code or "client_secret" not in session:
        flash("Login test failed or was cancelled -- try again.")
        return redirect(url_for("wizard.oauth"))

    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)

    try:
        tokens = await exchange_oauth_code(
            client_id=state["discord_client_id"],
            client_secret=session["client_secret"],
            redirect_uri=state["discord_oauth_redirect_uri"],
            code=code,
        )
        await get_current_user(tokens["access_token"])
        await get_current_user_guilds(tokens["access_token"])
    except (DiscordRestError, KeyError):
        flash("Login test failed -- check the client secret and try again.")
        return redirect(url_for("wizard.oauth"))

    session["oauth_verified"] = True
    flash("Login test succeeded.")
    return redirect(url_for("wizard.oauth"))


@bp.route("/finish", methods=["GET", "POST"])
async def finish():
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        state = await wizard_queries.get_or_create_wizard_state(conn)

    if state["step"] == "complete":
        return render_template("wizard_finish.html", already_complete=True)

    ready = bool(
        "bot_token" in session and "client_secret" in session and session.get("oauth_verified")
    )

    if request.method == "GET":
        return render_template("wizard_finish.html", ready=ready, already_complete=False)

    if not ready:
        flash("Complete the login test on the previous step first.")
        return redirect(url_for("wizard.oauth"))

    updates = {
        "DISCORD_BOT_TOKEN": session["bot_token"],
        "DISCORD_CLIENT_ID": state["discord_client_id"],
        "DISCORD_CLIENT_SECRET": session["client_secret"],
        "DISCORD_OAUTH_REDIRECT_URI": state["discord_oauth_redirect_uri"],
        "DISCORD_TEST_GUILD_ID": str(state["discord_guild_id"]),
    }
    if not os.environ.get("FLASK_SECRET_KEY"):
        updates["FLASK_SECRET_KEY"] = _secrets.token_hex(32)

    env_path = current_app.config["ENV_FILE_PATH"]
    write_env_updates(env_path, updates)

    # Unconditional override, not config.reload_env_file()'s blank-only
    # fill: this file was JUST written above, so its values must always win
    # over whatever's currently in os.environ (including a stale blank
    # placeholder), which is a different need than reload_env_file()'s
    # job of safely recovering settings in a freshly restarted process
    # without clobbering a real, deliberately-set value like DATABASE_URL.
    load_dotenv(dotenv_path=env_path, override=True)

    try:
        new_settings = load_settings()
    except ConfigError as e:
        flash(f"Config still incomplete after writing .env: {e}")
        return redirect(url_for("wizard.oauth"))

    async with pool.connection() as conn:
        await wizard_queries.update_wizard_state(conn, step="complete")

    on_complete = current_app.config.get("ON_COMPLETE")
    if on_complete is not None:
        on_complete(new_settings)

    return render_template("wizard_finish.html", already_complete=False, just_finished=True)


@bp.route("/pitch-kit")
async def pitch_kit():
    return render_template("wizard_pitch_kit.html")
