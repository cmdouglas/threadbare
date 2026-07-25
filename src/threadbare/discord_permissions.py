"""Pure Discord permission-bit math -- no discord.py, no psycopg, no I/O.
Kept top-level and dependency-free (mirrors pagination.py/urls.py's own
"pure top-level module" convention) specifically so the web app's setup
wizard can reuse compute_is_public()/REQUIRED_PERMISSIONS without dragging
discord.py into the web process -- web/discord_rest.py's own docstring
states that decoupling is a deliberate project invariant, not a preference.

sync_worker/permissions.py keeps the discord.py/psycopg-touching functions
that surround this math (refresh_channel_public_status, everyone_overwrite,
should_sync) -- this module is the pure subset extracted out of it, not a
replacement for it. Callers import the pure names from here directly.

compute_effective_permissions() is the one shared implementation of
Discord's full permission-resolution order (DESIGN.md §7 Phase 2):
compute_is_public (below) and wizard/preflight.compute_bot_effective_permissions
both delegate to it now, rather than each reimplementing the same
category/channel-tier combination logic for their own narrower identity
(@everyone-only, bot-only respectively).
"""

from dataclasses import dataclass
from typing import Protocol

# Discord permission bit flags (Discord API docs, PERMISSIONS bitwise flags).
VIEW_CHANNEL = 1 << 10
READ_MESSAGE_HISTORY = 1 << 16
ADMINISTRATOR = 1 << 3

REQUIRED_PERMISSIONS = VIEW_CHANNEL | READ_MESSAGE_HISTORY

# The "everything is granted" sentinel Administrator short-circuits to. Only
# ever compared against specific bits with `&`, never shown or stored, so the
# exact width just needs to stay at or above Discord's highest permission bit
# (~46 as of Discord's current docs); 49 leaves a few bits of headroom for
# ones they add later, and needs no maintenance when they do.
_ALL_PERMISSIONS = (1 << 49) - 1


class OverwriteLike(Protocol):
    """Structural type for anything carrying a permission overwrite's raw
    allow/deny bitfields. Callers with their own row/object shapes (discord.py
    objects, Discord REST JSON, Postgres rows) satisfy this without importing
    anything; RawOverwrite below is the concrete adapter for callers that just
    need a value to pass along.
    """

    allow: int
    deny: int


@dataclass(frozen=True)
class RawOverwrite:
    """The one concrete OverwriteLike in the project. Three private,
    byte-identical copies of this used to exist (in sync_worker/permissions.py,
    channel_visibility.py, and -- with two extra fields -- wizard/preflight.py),
    which structural typing kept working and therefore hid.
    """

    allow: int
    deny: int


def apply_overwrite(permissions: int, overwrite: OverwriteLike | None) -> int:
    if overwrite is None:
        return permissions
    return (permissions & ~overwrite.deny) | overwrite.allow


@dataclass(frozen=True)
class OverwriteTier:
    """One permission tier's (category's or channel's) already-classified
    overwrites. role_overwrites must already be filtered by the caller to
    only overwrites that apply to the identity being resolved (e.g. roles
    that identity actually holds) -- compute_effective_permissions applies
    whatever is here unconditionally, same as it trusts base_permissions to
    already be OR'd across that identity's roles.
    """

    everyone_overwrite: OverwriteLike | None = None
    role_overwrites: tuple[OverwriteLike, ...] = ()
    member_overwrite: OverwriteLike | None = None


_EMPTY_TIER = OverwriteTier()


def _apply_tier(permissions: int, tier: OverwriteTier) -> int:
    permissions = apply_overwrite(permissions, tier.everyone_overwrite)
    if tier.role_overwrites:
        combined_allow = 0
        combined_deny = 0
        for overwrite in tier.role_overwrites:
            combined_allow |= overwrite.allow
            combined_deny |= overwrite.deny
        permissions = (permissions & ~combined_deny) | combined_allow
    return apply_overwrite(permissions, tier.member_overwrite)


def compute_effective_permissions(
    base_permissions: int,
    category: OverwriteTier = _EMPTY_TIER,
    channel: OverwriteTier = _EMPTY_TIER,
) -> int:
    """Discord's full permission-resolution order: base permissions
    (already OR'd across whatever roles the identity being resolved holds
    -- the caller's job, not this function's) -> Administrator
    short-circuit (bypasses every overwrite) -> category tier (@everyone
    overwrite -> combined applicable-role overwrites -> member-specific
    overwrite) -> channel tier (same three sub-steps, applied last so a bit
    set there always wins over the same bit set at the category level).
    """
    if base_permissions & ADMINISTRATOR:
        return _ALL_PERMISSIONS
    permissions = _apply_tier(base_permissions, category)
    permissions = _apply_tier(permissions, channel)
    return permissions


def compute_is_public(
    default_role_permissions: int,
    category_overwrite: OverwriteLike | None,
    channel_overwrite: OverwriteLike | None,
) -> bool:
    """Whether @everyone can view the channel and read its history.

    The @everyone-only case of compute_effective_permissions: @everyone
    never holds roles or has a member-specific overwrite, so only each
    tier's everyone_overwrite slot is ever populated here.
    """
    permissions = compute_effective_permissions(
        default_role_permissions,
        category=OverwriteTier(everyone_overwrite=category_overwrite),
        channel=OverwriteTier(everyone_overwrite=channel_overwrite),
    )
    return (permissions & REQUIRED_PERMISSIONS) == REQUIRED_PERMISSIONS
