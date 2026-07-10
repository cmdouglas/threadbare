from flask import Flask

from threadbare.config import Settings
from threadbare.web.app import create_app


def _settings() -> Settings:
    return Settings(
        discord_bot_token="tok", discord_guild_id=1, database_url="postgresql://x/y"
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
