"""Authorization for the OAuth login gate and the mod-only admin blueprint.

has_mod_permissions/requires_login are pure and unit-testable without a
Flask request context; mod_required and is_logged_in need `session` (only
meaningful inside a request), matching web/views/*.py's existing pattern of
keeping I/O-touching code thin around pure logic.

resolve_visible_channel_ids is a newer, DB-touching addition: the per-user
channel-visibility set (DESIGN.md §7 Phase 2), the eventual replacement for
this module's binary is-a-guild-member gate for channels enrolled in
role-gating. Lives here rather than db/queries.py because it's
orchestration (several queries plus channel_visibility's pure resolution),
not a single query -- and here rather than a new module because this
module's own binary gate is literally what it's meant to replace. Wired in
via web/app.py's resolve_visible_channels before_request hook, which stashes
the result on g.visible_channel_ids for board.py/search.py/topic.py/user.py
to consult through channel_passes_visibility_gate.
"""

import logging
from functools import wraps

from flask import abort, session

from threadbare import channel_visibility
from threadbare.db import queries

logger = logging.getLogger(__name__)

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


def channel_passes_visibility_gate(channel: dict, visible_channel_ids: set[int]) -> bool:
    """True if `channel` (a queries.get_channel row, including
    visibility_enrolled) should be shown to this requester on direct
    board/topic navigation. Non-enrolled (default) channels are always
    True here -- the existing "no check at all" v1 gap for direct nav on
    non-enrolled channels is intentionally unchanged. An enrolled channel
    is gated purely by membership in the requester's visible_channel_ids;
    is_public/indexed don't factor in here, since enrollment supersedes
    them for this check.
    """
    if not channel["visibility_enrolled"]:
        return True
    return channel["id"] in visible_channel_ids


async def resolve_visible_channel_ids(conn, *, guild_id: int, user_id: int) -> set[int]:
    """The per-user channel-visibility set (DESIGN.md §7 Phase 2) --
    computed fresh from Postgres on every call, no session caching, no
    invalidation logic since nothing is cached (mirrors web/app.py's
    resolve_site_title reasoning: a permission change should show up
    immediately, not on some refresh timer). Called once per request by
    web/app.py's before_request hook for every logged-in visit.
    """
    user = await queries.get_user(conn, user_id)
    if user is None:
        # A likely symptom of a role-import gap: the bulk member-role
        # backfill (sync_worker/discovery.discover_member_roles) either
        # hasn't run yet or never picked up this member, so there's no
        # users row to read role_ids off at all -- not merely "role_ids is
        # empty", but no row whatsoever. Falls back to no roles held, same
        # as an unrecognized/departed member would.
        logger.warning(
            "resolve_visible_channel_ids: no users row for user_id=%s (guild=%s) -- "
            "falling back to no roles held; check whether the member-role "
            "backfill has run",
            user_id,
            guild_id,
        )
        role_ids = []
    else:
        role_ids = user["role_ids"]

    base_permissions = await queries.get_base_permissions(
        conn, guild_id=guild_id, role_ids=role_ids
    )
    logger.debug(
        "resolve_visible_channel_ids: user_id=%s guild_id=%s role_ids=%s base_permissions=%#x",
        user_id,
        guild_id,
        role_ids,
        base_permissions,
    )
    channels = await queries.get_visibility_channels(conn, guild_id=guild_id)

    category_ids = {c["parent_id"] for c in channels if c["parent_id"] is not None}
    all_ids = list({c["id"] for c in channels} | category_ids)

    role_overwrites = await queries.get_channel_role_overwrites(
        conn, channel_ids=all_ids, role_ids=[guild_id, *role_ids]
    )
    member_overwrites = await queries.get_channel_member_overwrites(
        conn, channel_ids=all_ids, user_id=user_id
    )

    return channel_visibility.compute_visible_channel_ids(
        base_permissions=base_permissions,
        everyone_role_id=guild_id,
        channels=channels,
        role_overwrites=role_overwrites,
        member_overwrites=member_overwrites,
    )
