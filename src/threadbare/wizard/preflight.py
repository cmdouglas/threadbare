"""The correctness-critical core of the setup wizard's channel preflight
check (DESIGN.md §8.2): resolving the BOT's own effective permissions per
channel, not @everyone's (that's discord_permissions.compute_is_public's
job). Pure functions over already-fetched REST JSON -- network calls stay
in web/discord_rest.py.

A thin wrapper over discord_permissions.compute_effective_permissions (the
one shared implementation of Discord's permission-resolution order, also
used by compute_is_public) -- this module's own job is narrower: adapting
Discord's REST-JSON overwrite shape (a single list tagged by type: 0=role/
1=member) into the OverwriteTier shape that function expects, for exactly
one identity (the bot).
"""

from dataclasses import dataclass
from typing import TypedDict

from threadbare.discord_permissions import (
    REQUIRED_PERMISSIONS,
    OverwriteTier,
    compute_effective_permissions,
)

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


def _tier_from_rest_overwrites(
    overwrites: list[RestOverwrite],
    *,
    everyone_role_id: int,
    role_ids: set[int],
    user_id: int,
) -> OverwriteTier:
    """Classifies one tier's (category's or channel's) tagged REST overwrite
    list into the three pre-classified slots compute_effective_permissions
    expects -- the caller-filters-first contract that function documents:
    role_overwrites is filtered down to only roles this identity actually
    holds (role_ids) here, not inside the shared function.
    """
    everyone_overwrite = next(
        (o for o in overwrites if o.type == ROLE_OVERWRITE_TYPE and o.id == everyone_role_id), None
    )
    role_overwrites = tuple(
        o
        for o in overwrites
        if o.type == ROLE_OVERWRITE_TYPE and o.id != everyone_role_id and o.id in role_ids
    )
    member_overwrite = next(
        (o for o in overwrites if o.type == MEMBER_OVERWRITE_TYPE and o.id == user_id), None
    )
    return OverwriteTier(
        everyone_overwrite=everyone_overwrite,
        role_overwrites=role_overwrites,
        member_overwrite=member_overwrite,
    )


def compute_bot_effective_permissions(
    *,
    base_permissions: int,
    everyone_role_id: int,
    bot_role_ids: set[int],
    bot_user_id: int,
    category_overwrites: list[RestOverwrite],
    channel_overwrites: list[RestOverwrite],
) -> int:
    """The BOT's own identity specifically (not @everyone, unlike
    discord_permissions.compute_is_public) -- base_permissions is expected
    to already be the guild @everyone permissions OR'd with every role the
    bot has. Adapts this module's REST-JSON overwrite shape into
    OverwriteTier, then delegates the actual resolution order to the shared
    compute_effective_permissions.
    """
    category = _tier_from_rest_overwrites(
        category_overwrites,
        everyone_role_id=everyone_role_id,
        role_ids=bot_role_ids,
        user_id=bot_user_id,
    )
    channel = _tier_from_rest_overwrites(
        channel_overwrites,
        everyone_role_id=everyone_role_id,
        role_ids=bot_role_ids,
        user_id=bot_user_id,
    )
    return compute_effective_permissions(base_permissions, category=category, channel=channel)


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
