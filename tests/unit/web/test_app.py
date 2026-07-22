from flask import Flask
from werkzeug.test import EnvironBuilder

from threadbare.config import Settings
from threadbare.web.app import create_app


def _settings() -> Settings:
    return Settings(
        discord_bot_token="tok",
        discord_guild_id=1,
        database_url="postgresql://x/y",
        discord_client_id="client-id",
        discord_client_secret="client-secret",
        discord_oauth_redirect_uri="http://localhost:5000/oauth/callback",
        flask_secret_key="test-secret-key",
    )


def test_create_app_returns_a_flask_app():
    app = create_app(_settings(), pool=None)

    assert isinstance(app, Flask)


def test_create_app_stores_settings_and_pool():
    settings = _settings()
    pool = object()

    app = create_app(settings, pool)

    assert app.config["SETTINGS"] is settings
    assert app.config["POOL"] is pool


def test_create_app_sets_script_name_from_x_forwarded_prefix():
    # Exercises the wsgi_app callable directly (bypassing Flask dispatch/the
    # login gate) since ProxyFix wraps that, not routing -- this is a
    # subpath-deployment concern (Caddy's handle_path/header_up), not
    # something any real route touches directly.
    app = create_app(_settings(), pool=None)
    environ = EnvironBuilder(
        path="/__proxyfix_probe__", headers={"X-Forwarded-Prefix": "/mirror"}
    ).get_environ()

    app.wsgi_app(environ, lambda *a, **k: None)

    assert environ["SCRIPT_NAME"] == "/mirror"


def test_create_app_script_name_defaults_empty_without_the_header():
    app = create_app(_settings(), pool=None)
    environ = EnvironBuilder(path="/__proxyfix_probe__").get_environ()

    app.wsgi_app(environ, lambda *a, **k: None)

    assert environ.get("SCRIPT_NAME", "") == ""
