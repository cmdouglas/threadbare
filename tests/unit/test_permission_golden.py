"""Golden tests against real permission fixtures (DESIGN.md §7: "the highest
test-coverage bar in the codebase... the only place a bug is a disclosure
bug, not a rendering bug"). Unlike test_discord_permissions.py's synthetic
Overwrite dataclasses, every role permission bitfield and channel/category
overwrite here is real data exported from a live Discord server
(tests/fixtures/permission_golden.json, scripts/export_permission_golden.py)
-- a purpose-built server layout where each fixture channel isolates one of
Discord's actual permission-resolution edge cases, verified live against
discord.py's own permissions_for() at capture time (see that script's
docstring for the exact server setup).

Each scenario is exercised through compute_effective_permissions -- the one
shared implementation this whole codebase's real callers (compute_is_public,
wizard/preflight.py's bot-permission check, channel_visibility.py's
per-member visibility) all delegate to -- with a paired positive/negative
case per edge case: "the override that grants access is held" vs. "it
isn't", both against the exact same real overwrite data, not two different
fixtures.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from threadbare.discord_permissions import (
    ADMINISTRATOR,
    REQUIRED_PERMISSIONS,
    OverwriteTier,
    compute_effective_permissions,
)

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "permission_golden.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text())


@dataclass(frozen=True)
class _Overwrite:
    allow: int
    deny: int


def _role_id(name: str) -> int:
    return FIXTURE["everyone_role"]["id"] if name == "@everyone" else FIXTURE["roles"][name]["id"]


def _tier_for(entity: dict | None, held_role_ids: set[int]) -> OverwriteTier:
    """Classifies one fixture channel/category's exported overwrite rows
    into an OverwriteTier for the given held role ids -- the same
    caller-filters-first contract compute_effective_permissions documents
    (mirrors channel_visibility.py's _tier_for / wizard/preflight.py's
    _tier_from_rest_overwrites, adapted to this fixture's JSON shape).
    entity is None for "no category" (a top-level fixture channel).
    """
    if entity is None:
        return OverwriteTier()
    everyone_row = next((o for o in entity["overwrites"] if o["role"] == "@everyone"), None)
    role_rows = [
        o
        for o in entity["overwrites"]
        if o["role"] != "@everyone" and _role_id(o["role"]) in held_role_ids
    ]
    return OverwriteTier(
        everyone_overwrite=_Overwrite(everyone_row["allow"], everyone_row["deny"])
        if everyone_row
        else None,
        role_overwrites=tuple(_Overwrite(o["allow"], o["deny"]) for o in role_rows),
    )


def _resolve(channel_name: str, *, held_roles: set[str], extra_base_permissions: int = 0) -> bool:
    """Resolves whether an identity holding exactly `held_roles` (plus
    @everyone, always) can view+read the named fixture channel's real
    exported overwrite data. extra_base_permissions models a permission the
    identity holds that isn't captured as one of this fixture's own role
    rows -- used only for the admin scenario below, since Discord's
    Administrator can't be scoped to a single channel and this project's
    disposable test server shouldn't stay permanently elevated between
    fixture captures (see scripts/export_permission_golden.py).
    """
    channel = FIXTURE["channels"][channel_name]
    category = FIXTURE["categories"].get(channel["parent_category"])
    held_role_ids = {_role_id(name) for name in held_roles}

    base_permissions = FIXTURE["everyone_role"]["permissions"] | extra_base_permissions
    for name in held_roles:
        base_permissions |= FIXTURE["roles"][name]["permissions"]

    effective = compute_effective_permissions(
        base_permissions,
        category=_tier_for(category, held_role_ids),
        channel=_tier_for(channel, held_role_ids),
    )
    return (effective & REQUIRED_PERMISSIONS) == REQUIRED_PERMISSIONS


# --- Category-vs-channel precedence (golden-category-precedence: category
# denies threadbare_testing, channel allows it back) ---


def test_channel_level_allow_beats_category_level_deny_for_the_same_role():
    assert _resolve("golden-category-precedence", held_roles={"threadbare_testing"}) is True


def test_category_level_deny_alone_denies_without_the_channel_override():
    # Same real category overwrite, channel tier left empty -- proves the
    # category-level deny genuinely denies on its own, isolating what the
    # channel-level allow above is actually overriding.
    channel = FIXTURE["channels"]["golden-category-precedence"]
    category = FIXTURE["categories"][channel["parent_category"]]
    held_role_ids = {_role_id("threadbare_testing")}
    base_permissions = (
        FIXTURE["everyone_role"]["permissions"]
        | FIXTURE["roles"]["threadbare_testing"]["permissions"]
    )

    effective = compute_effective_permissions(
        base_permissions, category=_tier_for(category, held_role_ids), channel=OverwriteTier()
    )

    assert (effective & REQUIRED_PERMISSIONS) != REQUIRED_PERMISSIONS


# --- Explicit deny vs. allow (golden-deny-allow: @everyone denied,
# threadbare_testing allowed, same channel tier) ---


def test_role_level_allow_beats_everyone_level_deny_at_the_same_tier():
    assert _resolve("golden-deny-allow", held_roles={"threadbare_testing"}) is True


def test_everyone_level_deny_applies_to_an_identity_without_the_allow_role():
    assert _resolve("golden-deny-allow", held_roles=set()) is False


# --- Multiple roles combined (golden-multi-role: threadbare_testing
# denied, threadbare_testing_2 allowed, same channel tier) ---


def test_one_held_roles_allow_beats_another_held_roles_deny():
    assert (
        _resolve("golden-multi-role", held_roles={"threadbare_testing", "threadbare_testing_2"})
        is True
    )


def test_the_denying_role_alone_stays_denied_without_the_allowing_role():
    assert _resolve("golden-multi-role", held_roles={"threadbare_testing"}) is False


# --- Administrator short-circuit (golden-admin: @everyone denied both
# required bits entirely) ---


def test_administrator_short_circuits_a_channel_wide_deny():
    # threadbare_testing_2 is captured with no permissions of its own (see
    # this file's module docstring) -- modeling admin explicitly here,
    # rather than relying on the fixture's captured role value, is
    # deliberate: Administrator can't be scoped to one channel on Discord,
    # so keeping the live test server permanently elevated just for this
    # one case isn't worth it.
    assert (
        _resolve(
            "golden-admin",
            held_roles={"threadbare_testing_2"},
            extra_base_permissions=ADMINISTRATOR,
        )
        is True
    )


def test_without_administrator_the_channel_wide_deny_holds():
    assert _resolve("golden-admin", held_roles={"threadbare_testing_2"}) is False


def test_without_administrator_a_plain_member_is_denied_too():
    assert _resolve("golden-admin", held_roles=set()) is False
