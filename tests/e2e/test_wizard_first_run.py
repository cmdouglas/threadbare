"""First-run setup wizard e2e tests (ROADMAP.md §7). Real Discord calls are
avoided by monkeypatching threadbare.web.views.wizard's imported
discord_rest functions -- the same pattern test_attachments.py/test_auth.py
already use for integration tests, applied here to a background-thread
werkzeug server for the first time in this codebase. Confirmed empirically
(not assumed) by the first test below: monkeypatch.setattr mutates the
actual module object in the process, and the background thread reads that
same module object (same process, same GIL, no cross-process boundary), so
it sees the patched function immediately.

"Test login" (the real OAuth round trip) can't be driven through Discord's
actual website in a browser test without a real network call, so these
tests jump straight to /oauth/callback?code=... with the exchange functions
monkeypatched -- exercising the real callback route logic end to end
without requiring genuine network access to discord.com.
"""

from threadbare.web.views import wizard as wizard_view

CHANNEL_ID = "700001"
GUILD_ID = "700000"
BOT_USER_ID = "1"


def _stub_everything(monkeypatch, page):
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

    async def fake_exchange_oauth_code(**kwargs):
        return {"access_token": "test-access-token"}

    async def fake_get_current_user(token, **kwargs):
        return {"id": "1", "username": "mod"}

    async def fake_get_current_user_guilds(token, **kwargs):
        return [{"id": GUILD_ID}]

    monkeypatch.setattr(wizard_view, "get_bot_user", fake_get_bot_user)
    monkeypatch.setattr(wizard_view, "get_bot_guilds", fake_get_bot_guilds)
    monkeypatch.setattr(wizard_view, "get_guild_channels", fake_get_guild_channels)
    monkeypatch.setattr(wizard_view, "get_guild_roles", fake_get_guild_roles)
    monkeypatch.setattr(wizard_view, "get_guild_member", fake_get_guild_member)
    monkeypatch.setattr(
        wizard_view, "get_recent_channel_message", fake_get_recent_channel_message
    )
    monkeypatch.setattr(wizard_view, "exchange_oauth_code", fake_exchange_oauth_code)
    monkeypatch.setattr(wizard_view, "get_current_user", fake_get_current_user)
    monkeypatch.setattr(wizard_view, "get_current_user_guilds", fake_get_current_user_guilds)


def test_monkeypatch_is_visible_to_the_background_thread_server(
    page, unconfigured_live_server, monkeypatch
):
    async def fake_get_bot_user(token, **kwargs):
        return {"id": "1", "username": "mybot"}

    monkeypatch.setattr(wizard_view, "get_bot_user", fake_get_bot_user)

    page.goto(f"{unconfigured_live_server}/token")
    page.fill('input[name="bot_token"]', "tok123")
    page.fill('input[name="client_id"]', "cid")
    page.click("button[type=submit]")

    assert page.url == f"{unconfigured_live_server.base_url}/invite"


def test_wizard_first_run_completes_and_hands_off_for_restart(
    page, unconfigured_live_server, monkeypatch
):
    _stub_everything(monkeypatch, page)
    base = unconfigured_live_server.base_url

    page.goto(f"{base}/token")
    page.fill('input[name="bot_token"]', "real-bot-token")
    page.fill('input[name="client_id"]', "real-client-id")
    page.click("button[type=submit]")
    assert page.url == f"{base}/invite"

    page.click("button:has-text(\"check now\")")
    assert page.url == f"{base}/channels"

    page.check(f'input[name="indexed_channel_id"][value="{CHANNEL_ID}"]')
    page.click("button:has-text(\"Continue\")")
    assert page.url == f"{base}/oauth"

    page.fill('input[name="client_secret"]', "real-client-secret")
    page.click("button:has-text(\"Save\")")

    page.goto(f"{base}/oauth/callback?code=abc123")
    assert "succeeded" in page.content()

    page.click("button:has-text(\"Continue to finish setup\")")
    assert page.url.startswith(f"{base}/finish")

    page.click("button:has-text(\"Finish setup\")")
    assert "All set" in page.content()

    env_content = unconfigured_live_server.env_path.read_text()
    assert "DISCORD_BOT_TOKEN=real-bot-token" in env_content
    assert "DISCORD_CLIENT_SECRET=real-client-secret" in env_content

    settings = unconfigured_live_server.completed["settings"]
    assert settings.discord_bot_token == "real-bot-token"

    # This process doesn't hot-swap to the real forum app in-process anymore
    # (see conftest.py's unconfigured_live_server docstring) -- it tells the
    # operator the app is restarting itself instead, via a JS-free
    # meta-refresh back to "/". The actual restart-and-serve-the-real-app
    # half is proven for real, as a real subprocess, in
    # test_web_process_restart.py.
    assert 'http-equiv="refresh"' in page.content()
