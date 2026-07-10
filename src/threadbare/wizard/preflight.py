"""The correctness-critical core of the setup wizard's channel preflight
check (DESIGN.md §8.2): resolving the BOT's own effective permissions per
channel, not @everyone's (that's discord_permissions.compute_is_public's
job). Pure functions over already-fetched REST JSON -- network calls stay
in web/discord_rest.py.

DESIGN.md §7 Phase 2 already calls full permission-mirroring math "the
fiddliest code in the project"; this is a narrower, one-identity version of
the same problem (resolving effective permissions for exactly one member --
the bot -- rather than every guild member) and deserves comparably careful
fixture coverage, not just happy-path cases.
"""

from dataclasses import dataclass
from typing import TypedDict

from threadbare.discord_permissions import REQUIRED_PERMISSIONS, apply_overwrite

ADMINISTRATOR = 1 << 3
# A permission bitmask wide enough to cover every documented Discord
# permission bit (currently up to ~bit 46) -- used only as the
# "everything is granted" sentinel Administrator short-circuits to.
_ALL_PERMISSIONS = (1 << 49) - 1

ROLE_OVERWRITE_TYPE = 0
MEMBER_OVERWRITE_TYPE = 1


@dataclass(frozen=True)
class RestOverwrite:
    id: int
    type: int  # 0 = role, 1 = member
    allow: int
    deny: int


def parse_overwrites(raw: list[dict]) -> list[RestOverwrite]:
    """Discord's REST API returns permission_overwrites with id/allow/deny
    as strings (avoiding JS bigint precision loss) -- converts them to int.
    """
    return [
        RestOverwrite(
            id=int(o["id"]), type=int(o["type"]), allow=int(o["allow"]), deny=int(o["deny"])
        )
        for o in raw
    ]


def _apply_overwrite_tier(
    permissions: int,
    overwrites: list[RestOverwrite],
    *,
    everyone_role_id: int,
    role_ids: set[int],
    user_id: int,
) -> int:
    """One tier (category or channel) of Discord's overwrite resolution:
    @everyone overwrite, then every applicable role overwrite combined
    (all denies first, then all allows -- Discord's documented role-overwrite
    merge order), then the member-specific overwrite for this exact user id.
    """
    everyone_overwrite = next(
        (o for o in overwrites if o.type == ROLE_OVERWRITE_TYPE and o.id == everyone_role_id), None
    )
    permissions = apply_overwrite(permissions, everyone_overwrite)

    role_overwrites = [
        o
        for o in overwrites
        if o.type == ROLE_OVERWRITE_TYPE and o.id != everyone_role_id and o.id in role_ids
    ]
    if role_overwrites:
        combined_deny = 0
        combined_allow = 0
        for o in role_overwrites:
            combined_deny |= o.deny
            combined_allow |= o.allow
        permissions = (permissions & ~combined_deny) | combined_allow

    member_overwrite = next(
        (o for o in overwrites if o.type == MEMBER_OVERWRITE_TYPE and o.id == user_id), None
    )
    permissions = apply_overwrite(permissions, member_overwrite)

    return permissions


def compute_bot_effective_permissions(
    *,
    base_permissions: int,
    everyone_role_id: int,
    bot_role_ids: set[int],
    bot_user_id: int,
    category_overwrites: list[RestOverwrite],
    channel_overwrites: list[RestOverwrite],
) -> int:
    """Discord's documented resolution order for the BOT's own identity
    specifically (not @everyone, unlike discord_permissions.compute_is_public):
    base (guild @everyone permissions OR'd with every role the bot has) ->
    Administrator short-circuit (bypasses every overwrite) -> category
    overwrites (@everyone, then combined role overwrites, then the bot's own
    member overwrite) -> the same three-step application for the channel's
    own overwrites, which always wins over category on a shared bit.
    """
    if base_permissions & ADMINISTRATOR:
        return _ALL_PERMISSIONS

    permissions = base_permissions
    permissions = _apply_overwrite_tier(
        permissions, category_overwrites,
        everyone_role_id=everyone_role_id, role_ids=bot_role_ids, user_id=bot_user_id,
    )
    permissions = _apply_overwrite_tier(
        permissions, channel_overwrites,
        everyone_role_id=everyone_role_id, role_ids=bot_role_ids, user_id=bot_user_id,
    )
    return permissions


class ChannelPermissionResult(TypedDict):
    channel_id: int
    ok: bool
    overwrite_denied: bool


def compute_channel_permission_table(
    *,
    base_permissions: int,
    everyone_role_id: int,
    bot_role_ids: set[int],
    bot_user_id: int,
    channels: list[dict],
    category_overwrites: dict[int, list[RestOverwrite]],
) -> list[ChannelPermissionResult]:
    """channels: [{"id", "parent_id", "overwrites": [RestOverwrite, ...]}].
    category_overwrites: category channel id -> its own overwrites list.

    overwrite_denied distinguishes "the guild-level grant was already
    insufficient" (False) from "the guild-level grant was sufficient, but a
    specific category/channel overwrite denies the bot" (True) -- DESIGN.md
    §8.2 requires calling these out distinctly to mods debugging a failed
    preflight check.
    """
    base_ok = (base_permissions & REQUIRED_PERMISSIONS) == REQUIRED_PERMISSIONS
    results: list[ChannelPermissionResult] = []
    for channel in channels:
        effective = compute_bot_effective_permissions(
            base_permissions=base_permissions,
            everyone_role_id=everyone_role_id,
            bot_role_ids=bot_role_ids,
            bot_user_id=bot_user_id,
            category_overwrites=category_overwrites.get(channel["parent_id"], []),
            channel_overwrites=channel["overwrites"],
        )
        ok = (effective & REQUIRED_PERMISSIONS) == REQUIRED_PERMISSIONS
        results.append(
            {
                "channel_id": channel["id"],
                "ok": ok,
                "overwrite_denied": (not ok) and base_ok,
            }
        )
    return results


def message_content_intent_ok(sample_message: dict | None) -> bool | None:
    """True iff a recently fetched message's content is non-empty. None (no
    messages in the channel yet) is inconclusive, not a failure -- the
    caller should render "not yet verifiable" rather than a red X.
    """
    if sample_message is None:
        return None
    return bool(sample_message.get("content"))
