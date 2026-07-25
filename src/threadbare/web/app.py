"""Flask app factory. SSR only (CLAUDE.md) — no client-side framework.
Views live under web/views/ and register themselves as blueprints here.
"""

from flask import Flask, g, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from threadbare import pagination, theme_bundle, urls
from threadbare.config import Settings
from threadbare.db import queries
from threadbare.rendering import avatars, emoji, relative_time, user_display
from threadbare.web import authz, board_tree, preferences, theme_storage, themes
from threadbare.web.views.admin import bp as admin_bp
from threadbare.web.views.admin_themes import bp as admin_themes_bp
from threadbare.web.views.attachments import bp as attachments_bp
from threadbare.web.views.auth import bp as auth_bp
from threadbare.web.views.board import bp as board_bp
from threadbare.web.views.board_index import bp as board_index_bp
from threadbare.web.views.preferences import bp as preferences_bp
from threadbare.web.views.search import bp as search_bp
from threadbare.web.views.themes import bp as themes_bp
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


def _theme_stylesheet_url() -> str:
    """The href for the resolved theme's stylesheet: Flask's static handler
    for a built-in, or the DB/volume-backed serving route for a custom theme
    (cache-busted by the theme's updated_at so a re-upload is picked up).
    """
    slug = g.theme
    if slug in themes.AVAILABLE_THEMES:
        return url_for("static", filename=themes.AVAILABLE_THEMES[slug])
    version = int(g.custom_theme_meta[slug]["updated_at"].timestamp())
    return url_for("themes.custom_asset", slug=slug, asset_path="theme.css", v=version)


def create_app(settings: Settings, pool) -> Flask:
    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config["POOL"] = pool
    app.secret_key = settings.flask_secret_key
    # Cap request bodies so a giant custom-theme upload is rejected by
    # Werkzeug before the handler ever reads it into memory (a small margin
    # over the bundle cap for multipart overhead). Every other POST is tiny.
    app.config["MAX_CONTENT_LENGTH"] = theme_bundle.MAX_BUNDLE_BYTES + (1 << 20)
    # Trusts X-Forwarded-Prefix from Caddy so url_for(...) emits correctly
    # prefixed links when self-hosting.md's subpath deployment option is in
    # use -- a no-op (SCRIPT_NAME stays "") when the header isn't sent, so
    # a root deployment is unaffected.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)

    app.jinja_env.globals["urls"] = urls
    app.jinja_env.globals["avatars"] = avatars
    app.jinja_env.globals["emoji"] = emoji
    app.jinja_env.globals["user_display"] = user_display
    app.jinja_env.globals["relative_time"] = relative_time
    app.jinja_env.globals["pagination"] = pagination
    app.jinja_env.globals["board_tree"] = board_tree
    app.jinja_env.globals["guild_id"] = settings.discord_guild_id

    def _serves_a_static_asset() -> bool:
        """True for the two endpoints that send a file off disk and read nothing
        from `g` (see web/views/themes.py's custom_asset).

        The DB-touching hooks below skip these. web/db.py opens a brand-new
        connection per .connection() call -- no pool survives Flask's
        async_to_sync bridge -- so the three of them cost three connections and
        ~7 queries. Paying that per *asset* is the part that actually bites: a
        custom theme is deliberately a "maximalist" media bundle (Phase 3), so
        one page paint can pull twenty images, fonts and audio files, and each
        one was running the full Phase-2 per-user visibility computation to
        serve a PNG.
        """
        return request.endpoint in ("static", "themes.custom_asset")

    @app.before_request
    async def resolve_current_theme():
        if _serves_a_static_asset():
            return
        # Custom themes are merged in from the DB, filtered to those whose
        # bundle actually exists on the themes volume -- so a lost/rebuilt
        # volume (the DB dump doesn't capture it) degrades to the built-in
        # themes rather than emitting broken stylesheet links. Fetched fresh
        # per request, same no-caching cost model as resolve_site_title.
        async with pool.connection() as conn:
            custom = await queries.get_custom_themes(conn)
        custom_meta = {
            t["slug"]: t
            for t in custom
            if theme_storage.theme_css_exists(settings.theme_storage_dir, t["slug"])
        }
        g.custom_theme_meta = custom_meta
        g.available_theme_slugs = set(themes.AVAILABLE_THEMES) | set(custom_meta)
        # Built-ins first (label == slug), then custom themes (label ==
        # display_name) -- the shape the preferences switcher iterates.
        g.theme_options = [{"slug": s, "label": s} for s in themes.AVAILABLE_THEMES] + [
            {"slug": s, "label": custom_meta[s]["display_name"]} for s in custom_meta
        ]
        g.theme = themes.resolve_theme(
            query_param=request.args.get("theme"),
            cookie_value=request.cookies.get(themes.THEME_COOKIE_NAME),
            available=g.available_theme_slugs,
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
        if _serves_a_static_asset():
            return
        # Queried fresh per request rather than cached, so a guild rename is
        # reflected immediately -- one extra trivial single-row-PK lookup on
        # the request's own connection, consistent with this app's existing
        # no-pooling-across-requests cost model (see web/db.py's docstring).
        async with pool.connection() as conn:
            guild = await queries.get_guild(conn, settings.discord_guild_id)
        g.site_title = f"{guild['name']} (threadbare view)" if guild else "Threadbare"
        g.site_icon_url = avatars.guild_icon_url(guild["id"], guild["icon"]) if guild else None

    @app.before_request
    def require_login():
        if not authz.requires_login(request.endpoint):
            return None
        if not authz.is_logged_in():
            return redirect(url_for("auth.login"))
        return None

    @app.before_request
    async def resolve_visible_channels():
        # require_login (above) only gates on whether login is *required*
        # for this endpoint, not on whether the requester is logged in --
        # an exempt route (login/oauth_callback/static) still reaches
        # every before_request hook regardless of login state. So this
        # hook needs its own guard despite running last, or an anonymous
        # visit to /login would KeyError on session["user_id"].
        if _serves_a_static_asset() or not authz.is_logged_in():
            return None
        async with pool.connection() as conn:
            g.visible_channel_ids = await authz.resolve_visible_channel_ids(
                conn, guild_id=settings.discord_guild_id, user_id=session["user_id"]
            )
        return None

    @app.context_processor
    def inject_theme_context():
        return {
            "theme": g.theme,
            "theme_stylesheet_url": _theme_stylesheet_url(),
            "themes_available": g.theme_options,
            "theme_switch_url": _theme_switch_url,
            "site_title": g.site_title,
            "site_icon_url": g.site_icon_url,
            "show_avatars": g.show_avatars,
            "avatar_toggle_href": _avatar_toggle_url(),
            "posts_per_page": g.posts_per_page,
            "posts_per_page_options": preferences.POSTS_PER_PAGE_OPTIONS,
            "posts_per_page_switch_url": _posts_per_page_switch_url,
        }

    @app.after_request
    def persist_theme_choice(response):
        requested = request.args.get("theme")
        # Accept any currently-valid slug (built-in or a registered custom
        # theme), so a custom ?theme= persists too. The getattr default is
        # load-bearing, not defensive noise: if resolve_current_theme raises
        # (a DB hiccup, say), Flask still runs after_request over the error
        # handler's response, and g never got populated -- without it a 500
        # page would itself die with an AttributeError.
        if requested in getattr(g, "available_theme_slugs", ()):
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
    app.register_blueprint(admin_themes_bp)
    app.register_blueprint(board_index_bp)
    app.register_blueprint(preferences_bp)
    app.register_blueprint(board_bp)
    app.register_blueprint(topic_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(attachments_bp)
    app.register_blueprint(themes_bp)

    return app
