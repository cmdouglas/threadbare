"""Authorization for the OAuth login gate and the mod-only admin blueprint.

has_mod_permissions/requires_login are pure and unit-testable without a
Flask request context; mod_required and is_logged_in need `session` (only
meaningful inside a request), matching web/views/*.py's existing pattern of
keeping I/O-touching code thin around pure logic.
"""

from functools import wraps

from flask import abort, session

# Discord permission bit flags (Discord API docs, PERMISSIONS bitwise
# flags), matching sync_worker/permissions.py's naming convention.
MANAGE_GUILD = 1 << 5
ADMINISTRATOR = 1 << 3

MOD_PERMISSIONS = MANAGE_GUILD | ADMINISTRATOR

# Routes that must stay reachable while logged out: the login gate itself
# would otherwise redirect a user trying to log in back to the login page.
LOGIN_EXEMPT_ENDPOINTS = frozenset({"auth.login", "auth.oauth_callback", "static"})


def has_mod_permissions(permissions: int) -> bool:
    """True if the bitfield includes Manage Server or Administrator --
    either is sufficient (Administrator implies Manage Server).
    """
    return bool(permissions & MOD_PERMISSIONS)


def requires_login(endpoint: str | None) -> bool:
    """Whether the global login gate applies to this endpoint. False only
    for the handful of routes reachable while logged out; True (gated) for
    everything else, including an unmatched route (endpoint is None).
    """
    return endpoint not in LOGIN_EXEMPT_ENDPOINTS


def is_logged_in() -> bool:
    return "user_id" in session


def mod_required(view):
    @wraps(view)
    async def wrapped(*args, **kwargs):
        if not session.get("is_mod", False):
            abort(403)
        return await view(*args, **kwargs)

    return wrapped
