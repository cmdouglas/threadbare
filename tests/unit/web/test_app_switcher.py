from threadbare.web.app_switcher import AppSwitcher


def _wsgi_app(marker):
    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [marker]

    return app


def _call(app):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status

    body = b"".join(app({}, start_response))
    return captured["status"], body


def test_app_switcher_dispatches_to_initial_app():
    switcher = AppSwitcher(_wsgi_app(b"initial"))

    status, body = _call(switcher)

    assert status == "200 OK"
    assert body == b"initial"


def test_app_switcher_dispatches_to_new_app_after_switch():
    switcher = AppSwitcher(_wsgi_app(b"initial"))

    switcher.switch_to(_wsgi_app(b"switched"))
    _, body = _call(switcher)

    assert body == b"switched"


def test_app_switcher_current_reflects_the_active_app():
    initial = _wsgi_app(b"initial")
    switcher = AppSwitcher(initial)
    assert switcher.current is initial

    switched = _wsgi_app(b"switched")
    switcher.switch_to(switched)
    assert switcher.current is switched
