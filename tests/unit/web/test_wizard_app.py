from flask import Flask
from werkzeug.test import EnvironBuilder

from threadbare.web.wizard_app import create_wizard_app


def test_create_wizard_app_returns_a_flask_app():
    app = create_wizard_app(pool=None)

    assert isinstance(app, Flask)


def test_create_wizard_app_sets_script_name_from_x_forwarded_prefix():
    # An unmatched path, not "/" -- every real wizard route (including "/")
    # is behind enforce_wizard_flow's before_request hook, which needs a
    # working pool; ProxyFix operates at the WSGI layer below routing, so an
    # unmatched path exercises it without touching the wizard flow at all.
    app = create_wizard_app(pool=None)
    environ = EnvironBuilder(
        path="/__proxyfix_probe__", headers={"X-Forwarded-Prefix": "/mirror"}
    ).get_environ()

    app.wsgi_app(environ, lambda *a, **k: None)

    assert environ["SCRIPT_NAME"] == "/mirror"


def test_create_wizard_app_script_name_defaults_empty_without_the_header():
    app = create_wizard_app(pool=None)
    environ = EnvironBuilder(path="/__proxyfix_probe__").get_environ()

    app.wsgi_app(environ, lambda *a, **k: None)

    assert environ.get("SCRIPT_NAME", "") == ""
