"""Flask app factory. SSR only (CLAUDE.md) — no client-side framework.
Views live under web/views/ and register themselves as blueprints here.
"""

from flask import Flask, g, redirect, request, url_for

from threadbare import urls
from threadbare.config import Settings
from threadbare.pagination import DEFAULT_PAGE_SIZE
from threadbare.web import authz, themes
from threadbare.web.views.admin import bp as admin_bp
from threadbare.web.views.attachments import bp as attachments_bp
from threadbare.web.views.auth import bp as auth_bp
from threadbare.web.views.board import bp as board_bp
from threadbare.web.views.board_index import bp as board_index_bp
from threadbare.web.views.search import bp as search_bp
from threadbare.web.views.topic import bp as topic_bp
from threadbare.web.views.user import bp as user_bp


def _theme_switch_url(name: str) -> str:
    """Rebuilds the current URL with `theme=<name>` merged in, so switching
    theme mid-browse doesn't lose the user's place (page number, search
    query, etc).
    """
    if request.endpoint is None:
        return request.path
    args = {**request.view_args, **request.args.to_dict(), "theme": name}
    return url_for(request.endpoint, **args)


def create_app(settings: Settings, pool) -> Flask:
    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config["POOL"] = pool
    app.secret_key = settings.flask_secret_key

    app.jinja_env.globals["urls"] = urls
    app.jinja_env.globals["guild_id"] = settings.discord_guild_id
    app.jinja_env.globals["default_page_size"] = DEFAULT_PAGE_SIZE

    @app.before_request
    def resolve_current_theme():
        g.theme = themes.resolve_theme(
            query_param=request.args.get("theme"),
            cookie_value=request.cookies.get(themes.THEME_COOKIE_NAME),
        )

    @app.before_request
    def require_login():
        if not authz.requires_login(request.endpoint):
            return None
        if not authz.is_logged_in():
            return redirect(url_for("auth.login"))
        return None

    @app.context_processor
    def inject_theme_context():
        return {
            "theme_stylesheet": themes.AVAILABLE_THEMES[g.theme],
            "themes_available": list(themes.AVAILABLE_THEMES),
            "theme_switch_url": _theme_switch_url,
        }

    @app.after_request
    def persist_theme_choice(response):
        requested = request.args.get("theme")
        if requested in themes.AVAILABLE_THEMES:
            response.set_cookie(
                themes.THEME_COOKIE_NAME,
                requested,
                max_age=themes.THEME_COOKIE_MAX_AGE,
                path="/",
                samesite="Lax",
            )
        return response

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(board_index_bp)
    app.register_blueprint(board_bp)
    app.register_blueprint(topic_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(attachments_bp)

    return app
