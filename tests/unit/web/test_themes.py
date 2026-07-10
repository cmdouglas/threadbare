from threadbare.web.themes import AVAILABLE_THEMES, DEFAULT_THEME, resolve_theme


def test_vbulletin_dark_theme_is_registered_and_selectable():
    assert "vbulletin-dark" in AVAILABLE_THEMES
    assert resolve_theme(query_param="vbulletin-dark", cookie_value=None) == "vbulletin-dark"


def test_terminal_theme_is_registered_and_selectable():
    assert "terminal" in AVAILABLE_THEMES
    assert resolve_theme(query_param="terminal", cookie_value=None) == "terminal"


def test_resolve_theme_returns_default_when_nothing_set():
    assert resolve_theme(query_param=None, cookie_value=None) == DEFAULT_THEME


def test_resolve_theme_uses_valid_cookie_value():
    assert resolve_theme(query_param=None, cookie_value="plain") == "plain"


def test_resolve_theme_falls_back_to_default_for_invalid_cookie_value():
    assert resolve_theme(query_param=None, cookie_value="bogus") == DEFAULT_THEME


def test_resolve_theme_query_param_overrides_cookie():
    assert resolve_theme(query_param="plain", cookie_value="subsilver") == "plain"


def test_resolve_theme_ignores_invalid_query_param_and_uses_cookie():
    assert resolve_theme(query_param="bogus", cookie_value="plain") == "plain"


def test_resolve_theme_ignores_invalid_query_param_with_no_cookie():
    assert resolve_theme(query_param="bogus", cookie_value=None) == DEFAULT_THEME
