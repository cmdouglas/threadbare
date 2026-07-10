"""Wizard resumability e2e tests (ROADMAP.md §7): ordinary bookmarked/
back-button resume, and the specific new requirement from this session --
session-loss resume, where only the bot token/OAuth client secret
(ephemeral, session-only) are lost, never the guided walkthrough progress,
bot invite, or channel confirmations (all persisted in wizard_state).

The third, process-boot-level resumability angle (an unconfigured install
serves the wizard's /intro instead of crashing) is covered by
tests/integration/web/test_cli.py rather than here -- it needs no browser,
and a real subprocess binding the hardcoded production port (5000) is both
unnecessary and, on this exact machine, flaky (macOS AirPlay Receiver
squats port 5000 by default, confirmed firsthand earlier this project).
"""

from threadbare.web.views import wizard as wizard_view

CHANNEL_ID = "700101"
GUILD_ID = "700100"
BOT_USER_ID = "1"


def _stub_discord(monkeypatch):
    async def fake_get_bot_user(token, **kwargs):
        return {"id": BOT_USER_ID, "username": "mybot"}

    async def fake_get_bot_guilds(token, **kwargs):
        return [{"id": GUILD_ID, "name": "Test Guild"}]

    async def fake_get_guild_channels(token, guild_id, **kwargs):
        return [
            {
                "id": CHANNEL_ID,
                "type": 0,
                "name": "general",
                "position": 0,
                "parent_id": None,
                "topic": None,
                "permission_overwrites": [],
            }
        ]

    async def fake_get_guild_roles(token, guild_id, **kwargs):
        return [{"id": guild_id, "permissions": str(1024 | 65536)}]

    async def fake_get_guild_member(token, guild_id, user_id, **kwargs):
        return {"user": {"id": BOT_USER_ID}, "roles": []}

    async def fake_get_recent_channel_message(token, channel_id, **kwargs):
        return {"id": "1", "content": "hi"}

    monkeypatch.setattr(wizard_view, "get_bot_user", fake_get_bot_user)
    monkeypatch.setattr(wizard_view, "get_bot_guilds", fake_get_bot_guilds)
    monkeypatch.setattr(wizard_view, "get_guild_channels", fake_get_guild_channels)
    monkeypatch.setattr(wizard_view, "get_guild_roles", fake_get_guild_roles)
    monkeypatch.setattr(wizard_view, "get_guild_member", fake_get_guild_member)
    monkeypatch.setattr(
        wizard_view, "get_recent_channel_message", fake_get_recent_channel_message
    )


def test_ordinary_resume_does_not_duplicate_channel_confirmations(
    page, unconfigured_live_server, monkeypatch
):
    _stub_discord(monkeypatch)
    base = unconfigured_live_server.base_url

    page.goto(f"{base}/token")
    page.fill('input[name="bot_token"]', "tok123")
    page.fill('input[name="client_id"]', "cid")
    page.click("button[type=submit]")
    page.click("button:has-text(\"check now\")")
    page.check(f'input[name="indexed_channel_id"][value="{CHANNEL_ID}"]')
    page.click("button:has-text(\"Continue\")")
    assert page.url == f"{base}/oauth"

    # Revisiting an earlier step (e.g. a bookmark to /intro) is fine -- it's
    # "completed or earlier", not skipping ahead.
    page.goto(f"{base}/intro")
    assert page.url == f"{base}/intro"

    # And revisiting /channels itself must still show the prior confirmation
    # rather than resetting it.
    page.goto(f"{base}/channels")
    assert page.is_checked(f'input[name="indexed_channel_id"][value="{CHANNEL_ID}"]')


def test_session_loss_bounces_to_token_and_preserves_prior_progress(
    page, unconfigured_live_server, monkeypatch
):
    _stub_discord(monkeypatch)
    base = unconfigured_live_server.base_url

    page.goto(f"{base}/token")
    page.fill('input[name="bot_token"]', "tok123")
    page.fill('input[name="client_id"]', "cid")
    page.click("button[type=submit]")
    page.click("button:has-text(\"check now\")")
    page.check(f'input[name="indexed_channel_id"][value="{CHANNEL_ID}"]')
    page.click("button:has-text(\"Continue\")")
    assert page.url == f"{base}/oauth"

    # Simulate a restarted web process (or a returning-later operator):
    # the session cookie -- and with it the in-memory bot token -- is gone.
    page.context.clear_cookies()

    page.goto(f"{base}/channels")

    assert page.url == f"{base}/token"
    # The client ID collected earlier is non-secret and already persisted
    # in wizard_state -- pre-filled, not lost.
    assert page.input_value('input[name="client_id"]') == "cid"

    # Re-entering only the bot token advances straight back to wherever
    # progress had actually reached (/oauth here) -- not /intro, not
    # /invite, i.e. the flow doesn't restart just because the session did.
    page.fill('input[name="bot_token"]', "tok123-again")
    page.fill('input[name="client_id"]', "cid")
    page.click("button[type=submit]")

    assert page.url == f"{base}/oauth"

    # And the channel confirmation made before the session was lost is
    # still intact, not wiped by the bounce.
    page.goto(f"{base}/channels")
    assert page.is_checked(f'input[name="indexed_channel_id"][value="{CHANNEL_ID}"]')


