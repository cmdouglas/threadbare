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
the result on g.visible_channel_ids for every read path to consult.

Two consumers, deliberately different, and worth knowing which is which:

- board.py/topic.py (direct navigation to a known id) call
  channel_passes_visibility_gate below.
- search.py/user.py/board_index.py/attachments.py never call it -- they pass
  g.visible_channel_ids straight into db/queries.py, whose _visibility_clause
  applies the stricter SQL rule (see that function, and the note on the gate
  itself about how the two differ).
"""

import logging
from functools import wraps

from flask import abort, session

from threadbare import channel_visibility
from threadbare.db import queries
from threadbare.discord_permissions import ADMINISTRATOR

logger = logging.getLogger(__name__)

# ADMINISTRATOR comes from the dependency-free threadbare.discord_permissions
# rather than being redefined here -- that module exists precisely so the web
# app can share this math without importing discord.py. MANAGE_GUILD lives here
# because it's only ever a web-side (OAuth `guilds` scope) concern.
MANAGE_GUILD = 1 << 5

MOD_PERMISSIONS = MANAGE_GUILD | ADMINISTRATOR

# Routes that must stay reachable while logged out: the login gate itself
# would otherwise redirect a user trying to log in back to the login page.
LOGIN_EXEMPT_ENDPOINTS = frozenset(
    {"auth.login", "auth.oauth_callback", "static", "themes.custom_asset"}
)


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
    board/topic navigation.

    DELIBERATELY LAXER than db/queries._visibility_clause, which is what the
    listings, search, post-history and the attachment proxy apply. That clause
    requires `indexed AND (is_public OR (enrolled AND visible))`; this returns
    True unconditionally for any non-enrolled channel, so a channel that is
    un-indexed or non-public but not enrolled is hidden from every listing yet
    still renders on direct navigation to its id. That is the pre-Phase-2 v1
    behaviour, kept unchanged on purpose: enrollment is the opt-in that turns
    on real per-user filtering, and tightening this would change what existing
    installs expose without a mod asking for it (DESIGN.md's upgrade contract,
    rule 4).

    The cost of that choice is that "who may see this channel" has two
    implementations. If they're ever unified, the strict one is the one to keep,
    and it needs to be a deliberate, release-noted change rather than a
    refactor. Anything that serves real message *content* should use the strict
    rule regardless -- see queries.get_attachment_by_id, which does.
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
