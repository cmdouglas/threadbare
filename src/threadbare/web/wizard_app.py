"""A wholly separate Flask app for the first-run setup wizard (ROADMAP.md
§7, DESIGN.md §8) -- not a blueprint bolted onto create_app(). create_app()
reads settings.discord_guild_id/settings.flask_secret_key unconditionally,
and its before_request hooks (require_login -> auth.login, which itself
reads settings.discord_client_id) assume a fully populated Settings.
Threading Settings | None through every one of those call sites to make
them wizard-safe would be a materially riskier change to the already
well-tested forum/auth path than this small, additive mini-app. base.html
also assumes full site chrome (search, admin link, theme switcher,
login/logout) the wizard shouldn't show at all.
"""

import secrets
from collections.abc import Callable
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from threadbare.config import Settings
from threadbare.web.views.wizard import bp as wizard_bp

DEFAULT_ENV_FILE_PATH = Path(".env")


def create_wizard_app(
    pool,
    *,
    on_complete: Callable[[Settings], None] | None = None,
    env_file_path: Path | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["POOL"] = pool
    app.config["ON_COMPLETE"] = on_complete
    app.config["ENV_FILE_PATH"] = env_file_path or DEFAULT_ENV_FILE_PATH
    # Ephemeral: wizard sessions are short-lived by design. A mid-wizard
    # process restart loses the in-flight bot token/OAuth client secret
    # (session-only, per the "no secrets in Postgres" decision) but not the
    # Postgres-backed progress in wizard_state -- an explicit tradeoff, not
    # an oversight (see db/wizard_queries.py and migrations/0005).
    app.secret_key = secrets.token_hex(32)
    # See create_app()'s matching ProxyFix comment (web/app.py) -- same
    # subpath-deployment mechanism, needed here too since the wizard is
    # what's actually reachable on a fresh, unconfigured install.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)
    app.register_blueprint(wizard_bp)
    return app
