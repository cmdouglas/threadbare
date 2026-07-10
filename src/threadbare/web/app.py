"""Flask app factory. SSR only (CLAUDE.md) — no client-side framework.
Views live under web/views/ and register themselves as blueprints here.
"""

from flask import Flask

from threadbare import urls
from threadbare.config import Settings
from threadbare.pagination import DEFAULT_PAGE_SIZE
from threadbare.web.views.attachments import bp as attachments_bp
from threadbare.web.views.board import bp as board_bp
from threadbare.web.views.board_index import bp as board_index_bp
from threadbare.web.views.search import bp as search_bp
from threadbare.web.views.topic import bp as topic_bp
from threadbare.web.views.user import bp as user_bp


def create_app(settings: Settings, pool) -> Flask:
    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config["POOL"] = pool

    app.jinja_env.globals["urls"] = urls
    app.jinja_env.globals["guild_id"] = settings.discord_guild_id
    app.jinja_env.globals["default_page_size"] = DEFAULT_PAGE_SIZE

    app.register_blueprint(board_index_bp)
    app.register_blueprint(board_bp)
    app.register_blueprint(topic_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(attachments_bp)

    return app
