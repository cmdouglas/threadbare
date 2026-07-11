"""Unit-level test of cli.py's GunicornApplication wrapper in isolation --
no real server is started (Config/load()/load_config() only). The rest of
web/cli.py's branching (wizard vs. configured, host/workers env vars,
restart-on-finish scheduling) needs a real Postgres connection to exercise
config.is_configured()/wizard_state, so it lives in
tests/integration/web/test_cli.py instead. --version is the one exception:
it must short-circuit before any config/DB access at all, so it belongs
here rather than in the DB-backed integration tests.
"""

import pytest

import threadbare
from threadbare.web import cli
from threadbare.web.cli import GunicornApplication


def test_gunicorn_application_load_returns_the_given_app():
    app = object()
    gunicorn_app = GunicornApplication(app, {"bind": "127.0.0.1:5000", "workers": 4})

    assert gunicorn_app.load() is app


def test_gunicorn_application_load_config_applies_options_to_cfg():
    app = object()
    gunicorn_app = GunicornApplication(app, {"workers": 4, "bind": "0.0.0.0:5000"})

    assert gunicorn_app.cfg.settings["workers"].value == 4
    assert gunicorn_app.cfg.settings["bind"].value == ["0.0.0.0:5000"]


def test_gunicorn_application_defaults_options_to_empty_dict():
    app = object()
    # No options at all -- shouldn't raise, and gunicorn's own defaults apply.
    gunicorn_app = GunicornApplication(app)

    assert gunicorn_app.load() is app


def test_main_version_flag_prints_version_and_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["threadbare-web", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 0
    assert threadbare.__version__ in capsys.readouterr().out
