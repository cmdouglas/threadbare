import os

from threadbare.web.views import wizard as wizard_view

from .conftest import run

GUILD_ID = 999


async def _seed(web_conn, **fields):
    await wizard_view.wizard_queries.get_or_create_wizard_state(web_conn)
    await wizard_view.wizard_queries.update_wizard_state(web_conn, **fields)


def _seed_ready_to_finish(client, web_conn):
    run(
        _seed(
            web_conn,
            step="oauth",
            discord_client_id="cid",
            discord_guild_id=GUILD_ID,
            discord_oauth_redirect_uri="http://localhost/oauth/callback",
            channels_confirmed=True,
        )
    )
    with client.session_transaction() as sess:
        sess["bot_token"] = "real-bot-token"
        sess["client_secret"] = "real-client-secret"
        sess["oauth_verified"] = True


def test_finish_get_shows_ready_when_all_preconditions_met(wizard_client, web_conn):
    _seed_ready_to_finish(wizard_client, web_conn)

    resp = wizard_client.get("/finish")

    assert resp.status_code == 200
    assert b"Finish setup" in resp.data


def test_finish_get_shows_not_ready_when_oauth_not_verified(wizard_client, web_conn):
    run(_seed(web_conn, step="oauth", discord_client_id="cid", discord_guild_id=GUILD_ID))
    with wizard_client.session_transaction() as sess:
        sess["bot_token"] = "tok"

    resp = wizard_client.get("/finish")

    assert resp.status_code == 200
    assert b"Complete the login test" in resp.data


def test_finish_post_writes_env_file_and_calls_on_complete(
    wizard_app, wizard_client, web_conn, tmp_path
):
    # finish() deliberately calls load_dotenv(..., override=True) against
    # the real process os.environ (necessary so the newly written .env
    # values definitely take effect -- see its docstring), which would
    # otherwise leak into every later test in this session. Snapshot/restore
    # around this one test, since it's the only one that exercises that path.
    environ_snapshot = dict(os.environ)
    try:
        env_path = tmp_path / ".env"
        env_path.write_text("DATABASE_URL=postgresql://x\n")
        wizard_app.config["ENV_FILE_PATH"] = env_path

        completed_with = {}
        wizard_app.config["ON_COMPLETE"] = lambda settings: completed_with.update(settings=settings)

        _seed_ready_to_finish(wizard_client, web_conn)

        resp = wizard_client.post("/finish")

        assert resp.status_code == 200
        assert b"All set" in resp.data

        content = env_path.read_text()
        assert "DISCORD_BOT_TOKEN=real-bot-token" in content
        assert "DISCORD_CLIENT_SECRET=real-client-secret" in content
        assert "DISCORD_TEST_GUILD_ID=999" in content

        assert "settings" in completed_with
        assert completed_with["settings"].discord_bot_token == "real-bot-token"

        async def _fetch_state():
            return await wizard_view.wizard_queries.get_or_create_wizard_state(web_conn)

        state = run(_fetch_state())
        assert state["step"] == "complete"
    finally:
        os.environ.clear()
        os.environ.update(environ_snapshot)


def test_finish_post_meta_refresh_respects_forwarded_prefix(
    wizard_app, wizard_client, web_conn, tmp_path
):
    # Same real-os.environ mutation as the test above -- same snapshot/restore.
    environ_snapshot = dict(os.environ)
    try:
        env_path = tmp_path / ".env"
        env_path.write_text("DATABASE_URL=postgresql://x\n")
        wizard_app.config["ENV_FILE_PATH"] = env_path

        _seed_ready_to_finish(wizard_client, web_conn)

        resp = wizard_client.post("/finish", headers={"X-Forwarded-Prefix": "/discord-mirror"})

        assert resp.status_code == 200
        assert b'content="8;url=/discord-mirror/"' in resp.data
    finally:
        os.environ.clear()
        os.environ.update(environ_snapshot)


def test_finish_post_without_preconditions_redirects_to_oauth(wizard_client, web_conn):
    run(_seed(web_conn, step="oauth", discord_client_id="cid", discord_guild_id=GUILD_ID))
    with wizard_client.session_transaction() as sess:
        sess["bot_token"] = "tok"

    resp = wizard_client.post("/finish")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/oauth"


def test_finish_get_shows_already_complete_after_finishing(wizard_client, web_conn):
    run(_seed(web_conn, step="complete"))
    with wizard_client.session_transaction() as sess:
        sess["bot_token"] = "tok"
        sess["client_secret"] = "shh"

    resp = wizard_client.get("/finish")

    assert resp.status_code == 200
    assert b"already complete" in resp.data
