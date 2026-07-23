"""Cookie-backed display preferences -- pure logic, no Flask/request
coupling, mirroring web/themes.py's own shape. web/app.py wires this into a
before_request hook.

Cookie-backed rather than a DB-stored per-user preference: see
DESIGN.md §5 and ROADMAP.md's UI polish backlog for the planned migration to
account-level storage once logged in.
"""

AVATAR_COOKIE_NAME = "show_avatars"
AVATAR_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def resolve_show_avatars(*, query_param: str | None, cookie_value: str | None) -> bool:
    if query_param in ("on", "off"):
        return query_param == "on"
    if cookie_value in ("on", "off"):
        return cookie_value == "on"
    return True
