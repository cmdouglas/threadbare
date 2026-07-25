from threadbare.web.themes import AVAILABLE_THEMES, DEFAULT_THEME, resolve_theme

BUILT_INS = set(AVAILABLE_THEMES)


def test_vbulletin_dark_theme_is_registered_and_selectable():
    assert "vbulletin-dark" in AVAILABLE_THEMES
    assert (
        resolve_theme(query_param="vbulletin-dark", cookie_value=None, available=BUILT_INS)
        == "vbulletin-dark"
    )


def test_terminal_theme_is_registered_and_selectable():
    assert "terminal" in AVAILABLE_THEMES
    assert (
        resolve_theme(query_param="terminal", cookie_value=None, available=BUILT_INS) == "terminal"
    )


def test_resolve_theme_returns_default_when_nothing_set():
    assert resolve_theme(query_param=None, cookie_value=None, available=BUILT_INS) == DEFAULT_THEME


def test_resolve_theme_uses_valid_cookie_value():
    assert resolve_theme(query_param=None, cookie_value="plain", available=BUILT_INS) == "plain"


def test_resolve_theme_falls_back_to_default_for_invalid_cookie_value():
    assert (
        resolve_theme(query_param=None, cookie_value="bogus", available=BUILT_INS) == DEFAULT_THEME
    )


def test_resolve_theme_query_param_overrides_cookie():
    assert (
        resolve_theme(query_param="plain", cookie_value="subsilver", available=BUILT_INS) == "plain"
    )


def test_resolve_theme_ignores_invalid_query_param_and_uses_cookie():
    assert resolve_theme(query_param="bogus", cookie_value="plain", available=BUILT_INS) == "plain"


def test_resolve_theme_ignores_invalid_query_param_with_no_cookie():
    assert (
        resolve_theme(query_param="bogus", cookie_value=None, available=BUILT_INS) == DEFAULT_THEME
    )


def test_resolve_theme_accepts_a_custom_slug_present_in_available():
    available = set(AVAILABLE_THEMES) | {"my-custom"}
    assert (
        resolve_theme(query_param="my-custom", cookie_value=None, available=available)
        == "my-custom"
    )


def test_resolve_theme_falls_back_when_a_custom_slug_is_no_longer_available():
    # e.g. a custom theme the cookie still names was deleted -- not in the
    # current available set, so it degrades to the default (a built-in).
    available = set(AVAILABLE_THEMES)
    assert (
        resolve_theme(query_param=None, cookie_value="deleted-theme", available=available)
        == DEFAULT_THEME
    )
