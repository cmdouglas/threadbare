"""Cookie-backed display preferences -- pure logic, no Flask/request
coupling, mirroring web/themes.py's own shape. web/app.py wires this into a
before_request hook.

Cookie-backed rather than a DB-stored per-user preference: see
DESIGN.md §5 and ROADMAP.md's UI polish backlog for the planned migration to
account-level storage once logged in.
"""

AVATAR_COOKIE_NAME = "show_avatars"
AVATAR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

POSTS_PER_PAGE_COOKIE_NAME = "posts_per_page"
POSTS_PER_PAGE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
POSTS_PER_PAGE_OPTIONS = (10, 25, 50, 100)
DEFAULT_POSTS_PER_PAGE = 25


def resolve_show_avatars(*, query_param: str | None, cookie_value: str | None) -> bool:
    if query_param in ("on", "off"):
        return query_param == "on"
    if cookie_value in ("on", "off"):
        return cookie_value == "on"
    return True


def resolve_posts_per_page(*, query_param: str | None, cookie_value: str | None) -> int:
    for raw in (query_param, cookie_value):
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value in POSTS_PER_PAGE_OPTIONS:
            return value
    return DEFAULT_POSTS_PER_PAGE
