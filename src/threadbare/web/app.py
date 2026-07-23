"""Flask app factory. SSR only (CLAUDE.md) — no client-side framework.
Views live under web/views/ and register themselves as blueprints here.
"""

from flask import Flask, g, redirect, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from threadbare import pagination, urls
from threadbare.config import Settings
from threadbare.db import queries
from threadbare.rendering import avatars
from threadbare.web import authz, board_tree, preferences, themes
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


def _avatar_toggle_url() -> str:
    """Same arg-merging idea as _theme_switch_url, but there are only two
    states, so this just flips whatever's currently in effect.
    """
    if request.endpoint is None:
        return request.path
    next_value = "off" if g.show_avatars else "on"
    args = {**request.view_args, **request.args.to_dict(), "avatars": next_value}
    return url_for(request.endpoint, **args)


def _posts_per_page_switch_url(value: int) -> str:
    """Same arg-merging idea as _theme_switch_url."""
    if request.endpoint is None:
        return request.path
    args = {**request.view_args, **request.args.to_dict(), "posts_per_page": value}
    return url_for(request.endpoint, **args)


def create_app(settings: Settings, pool) -> Flask:
    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config["POOL"] = pool
    app.secret_key = settings.flask_secret_key
    # Trusts X-Forwarded-Prefix from Caddy so url_for(...) emits correctly
    # prefixed links when self-hosting.md's subpath deployment option is in
    # use -- a no-op (SCRIPT_NAME stays "") when the header isn't sent, so
    # a root deployment is unaffected.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)

    app.jinja_env.globals["urls"] = urls
    app.jinja_env.globals["avatars"] = avatars
    app.jinja_env.globals["pagination"] = pagination
    app.jinja_env.globals["board_tree"] = board_tree
    app.jinja_env.globals["guild_id"] = settings.discord_guild_id

    @app.before_request
    def resolve_current_theme():
        g.theme = themes.resolve_theme(
            query_param=request.args.get("theme"),
            cookie_value=request.cookies.get(themes.THEME_COOKIE_NAME),
        )

    @app.before_request
    def resolve_show_avatars():
        g.show_avatars = preferences.resolve_show_avatars(
            query_param=request.args.get("avatars"),
            cookie_value=request.cookies.get(preferences.AVATAR_COOKIE_NAME),
        )

    @app.before_request
    def resolve_posts_per_page():
        g.posts_per_page = preferences.resolve_posts_per_page(
            query_param=request.args.get("posts_per_page"),
            cookie_value=request.cookies.get(preferences.POSTS_PER_PAGE_COOKIE_NAME),
        )

    @app.before_request
    async def resolve_site_title():
        # Queried fresh per request rather than cached, so a guild rename is
        # reflected immediately -- one extra trivial single-row-PK lookup on
        # the request's own connection, consistent with this app's existing
        # no-pooling-across-requests cost model (see web/db.py's docstring).
        async with pool.connection() as conn:
            guild = await queries.get_guild(conn, settings.discord_guild_id)
        g.site_title = f"{guild['name']} (threadbare view)" if guild else "Threadbare"

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
            "site_title": g.site_title,
            "show_avatars": g.show_avatars,
            "avatar_toggle_href": _avatar_toggle_url(),
            "posts_per_page": g.posts_per_page,
            "posts_per_page_options": preferences.POSTS_PER_PAGE_OPTIONS,
            "posts_per_page_switch_url": _posts_per_page_switch_url,
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

    @app.after_request
    def persist_avatar_choice(response):
        requested = request.args.get("avatars")
        if requested in ("on", "off"):
            response.set_cookie(
                preferences.AVATAR_COOKIE_NAME,
                requested,
                max_age=preferences.AVATAR_COOKIE_MAX_AGE,
                path="/",
                samesite="Lax",
            )
        return response

    @app.after_request
    def persist_posts_per_page_choice(response):
        requested = request.args.get("posts_per_page", type=int)
        if requested in preferences.POSTS_PER_PAGE_OPTIONS:
            response.set_cookie(
                preferences.POSTS_PER_PAGE_COOKIE_NAME,
                str(requested),
                max_age=preferences.POSTS_PER_PAGE_COOKIE_MAX_AGE,
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
