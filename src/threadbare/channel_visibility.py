"""Computes which channels a guild member (identified by their own role_ids)
can currently view, from already-fetched Postgres rows -- DESIGN.md §7
Phase 2's per-user channel-visibility set. Pure and dependency-free, same
convention as discord_permissions.py/pagination.py/urls.py, so a future
non-web caller can reuse it without dragging in Flask.

This is the DB-shaped sibling of wizard/preflight.py's
_tier_from_rest_overwrites/compute_channel_permission_table, which do the
same per-channel assembly over Discord's REST-JSON-shaped overwrite list
instead of the two separate channel_role_overwrites/channel_member_overwrites
tables this module reads from.
"""

import logging
from dataclasses import dataclass

from threadbare.discord_permissions import (
    REQUIRED_PERMISSIONS,
    OverwriteTier,
    compute_effective_permissions,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Overwrite:
    """Wraps a plain dict_row into something with .allow/.deny attribute
    access, satisfying OverwriteLike -- DB rows only support ["allow"]
    indexing. Same gap wizard/preflight.py's RestOverwrite closes for its
    own REST-JSON rows.
    """

    allow: int
    deny: int


def _tier_for(
    tier_channel_id: int | None,
    *,
    role_overwrites_by_channel: dict[int, list[dict]],
    member_overwrites_by_channel: dict[int, dict],
    everyone_role_id: int,
    tier_name: str,
    channel_id: int,
) -> OverwriteTier:
    """tier_channel_id is a category's id (for the category tier) or a
    content channel's own id (for the channel tier); None (no category --
    channels.parent_id IS NULL) returns the empty tier, same as a
    category/channel with no overwrite rows at all. tier_name/channel_id
    are for logging only (DEBUG -- set LOG_LEVEL=DEBUG to see per-channel
    visibility troubleshooting detail), identifying which tier of which
    content channel's computation this call belongs to.
    """
    if tier_channel_id is None:
        logger.debug(
            "channel %s: %s tier has no id (no category) -- treating as empty",
            channel_id,
            tier_name,
        )
        return OverwriteTier()

    rows = role_overwrites_by_channel.get(tier_channel_id, [])
    everyone_row = next((r for r in rows if r["role_id"] == everyone_role_id), None)
    role_rows = tuple(r for r in rows if r["role_id"] != everyone_role_id)
    member_row = member_overwrites_by_channel.get(tier_channel_id)

    logger.debug(
        "channel %s: %s tier (id=%s) everyone=%s role_overwrites=%s member=%s",
        channel_id,
        tier_name,
        tier_channel_id,
        {"allow": everyone_row["allow"], "deny": everyone_row["deny"]} if everyone_row else None,
        [{"role_id": r["role_id"], "allow": r["allow"], "deny": r["deny"]} for r in role_rows],
        {"allow": member_row["allow"], "deny": member_row["deny"]} if member_row else None,
    )

    return OverwriteTier(
        everyone_overwrite=_Overwrite(everyone_row["allow"], everyone_row["deny"])
        if everyone_row
        else None,
        role_overwrites=tuple(_Overwrite(r["allow"], r["deny"]) for r in role_rows),
        member_overwrite=_Overwrite(member_row["allow"], member_row["deny"])
        if member_row
        else None,
    )


def compute_visible_channel_ids(
    *,
    base_permissions: int,
    everyone_role_id: int,
    channels: list[dict],
    role_overwrites: list[dict],
    member_overwrites: list[dict],
) -> set[int]:
    """channels must already be restricted to real content channels
    (channel_types.NON_CONTENT_TYPES excluded) -- categories only ever enter
    this computation via a content channel's parent_id, never as a
    visibility target of their own. role_overwrites/member_overwrites are
    expected pre-filtered by the caller to rows relevant to this identity
    (its own role_ids plus everyone_role_id, and its own user_id) -- the
    same caller-filters-first contract OverwriteTier's own docstring states.
    base_permissions is @everyone's permissions OR'd with every role this
    identity holds (the caller's job).
    """
    role_by_channel: dict[int, list[dict]] = {}
    for row in role_overwrites:
        role_by_channel.setdefault(row["channel_id"], []).append(row)
    member_by_channel = {row["channel_id"]: row for row in member_overwrites}

    visible: set[int] = set()
    for channel in channels:
        category = _tier_for(
            channel["parent_id"],
            role_overwrites_by_channel=role_by_channel,
            member_overwrites_by_channel=member_by_channel,
            everyone_role_id=everyone_role_id,
            tier_name="category",
            channel_id=channel["id"],
        )
        channel_tier = _tier_for(
            channel["id"],
            role_overwrites_by_channel=role_by_channel,
            member_overwrites_by_channel=member_by_channel,
            everyone_role_id=everyone_role_id,
            tier_name="channel",
            channel_id=channel["id"],
        )
        effective = compute_effective_permissions(
            base_permissions, category=category, channel=channel_tier
        )
        is_visible = (effective & REQUIRED_PERMISSIONS) == REQUIRED_PERMISSIONS
        logger.debug(
            "channel %s: base_permissions=%#x effective_permissions=%#x required=%#x visible=%s",
            channel["id"],
            base_permissions,
            effective,
            REQUIRED_PERMISSIONS,
            is_visible,
        )
        if is_visible:
            visible.add(channel["id"])

    return visible
