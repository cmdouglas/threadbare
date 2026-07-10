"""Theme selection -- pure logic, no Flask/request coupling, so it's testable
without a request context. web/app.py wires this into a before_request hook.
"""

AVAILABLE_THEMES = {
    "plain": "theme-plain.css",
    "subsilver": "theme-subsilver.css",
    "vbulletin-dark": "theme-vbulletin-dark.css",
}
DEFAULT_THEME = "subsilver"

THEME_COOKIE_NAME = "theme"
THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def resolve_theme(*, query_param: str | None, cookie_value: str | None) -> str:
    if query_param in AVAILABLE_THEMES:
        return query_param
    if cookie_value in AVAILABLE_THEMES:
        return cookie_value
    return DEFAULT_THEME
