"""Theme selection -- pure logic, no Flask/request coupling, so it's testable
without a request context. web/app.py wires this into a before_request hook.
"""

AVAILABLE_THEMES = {
    "plain": "theme-plain.css",
    "subsilver": "theme-subsilver.css",
    "vbulletin-dark": "theme-vbulletin-dark.css",
    "terminal": "theme-terminal.css",
}
DEFAULT_THEME = "subsilver"

THEME_COOKIE_NAME = "theme"
THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def resolve_theme(
    *,
    query_param: str | None,
    cookie_value: str | None,
    available: set[str] | None = None,
) -> str:
    """`available` is the set of currently-valid theme slugs -- the built-ins
    plus any registered custom themes whose bundle actually exists on disk
    (web/app.py supplies that combined set). Stays pure/DB-free by taking the
    set as a parameter rather than importing the DB here. Defaults to the
    built-ins alone when omitted (unit tests, any built-in-only caller). A
    slug that isn't in `available` -- including a custom theme that's since
    been deleted -- falls through to DEFAULT_THEME, which is always a built-in.
    """
    valid = set(AVAILABLE_THEMES) if available is None else available
    if query_param in valid:
        return query_param
    if cookie_value in valid:
        return cookie_value
    return DEFAULT_THEME
