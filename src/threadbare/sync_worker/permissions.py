from dataclasses import dataclass

import discord
import psycopg

from threadbare.discord_permissions import (
    READ_MESSAGE_HISTORY,
    REQUIRED_PERMISSIONS,
    VIEW_CHANNEL,
    compute_is_public,
)
from threadbare.sync_worker import repository
from threadbare.sync_worker.discord_types import OverwriteLike

# VIEW_CHANNEL/READ_MESSAGE_HISTORY/REQUIRED_PERMISSIONS/compute_is_public
# now live in the top-level, dependency-free threadbare.discord_permissions
# (so the web app's setup wizard can reuse them without importing discord.py
# into the web process) -- re-exported here so every existing caller of
# this module keeps working unchanged.
__all__ = [
    "VIEW_CHANNEL",
    "READ_MESSAGE_HISTORY",
    "REQUIRED_PERMISSIONS",
    "compute_is_public",
    "refresh_channel_public_status",
    "everyone_overwrite",
    "should_sync",
]


async def refresh_channel_public_status(
    conn: psycopg.AsyncConnection,
    *,
    channel_id: int,
    default_role_permissions: int,
    category_overwrite: OverwriteLike | None,
    channel_overwrite: OverwriteLike | None,
) -> bool:
    """Recompute is_public for a channel, purging its content if it just
    became non-public (DESIGN.md §3: no permission bypass — a channel that
    stops being @everyone-readable must lose its indexed content) *and*
    isn't visibility_enrolled -- an enrolled channel losing @everyone access
    is still meant to be synced and filtered at read time by the requester's
    real permissions (should_sync below), so purging it here would defeat
    the whole point of enrolling it. Returns the newly computed is_public
    value.
    """
    is_public = compute_is_public(default_role_permissions, category_overwrite, channel_overwrite)
    flags = await repository.get_channel_sync_flags(conn, channel_id)
    previously_public = flags[0] if flags is not None else None
    visibility_enrolled = flags[2] if flags is not None else False

    if previously_public and not is_public and not visibility_enrolled:
        await repository.purge_channel_content(conn, channel_id)

    await repository.set_channel_is_public(conn, channel_id, is_public)
    return is_public


@dataclass(frozen=True)
class _RawOverwrite:
    allow: int
    deny: int


def everyone_overwrite(target: discord.abc.GuildChannel) -> _RawOverwrite:
    """Extract the @everyone role's raw allow/deny overwrite ints off a
    live discord.py channel or category object — the adapter that bridges
    real Discord objects into compute_is_public()'s OverwriteLike inputs.
    Shared by events.py (live CHANNEL_UPDATE/role events) and discovery.py
    (initial channel discovery), so it lives here rather than in either.
    """
    overwrite = target.overwrites_for(target.guild.default_role)
    allow, deny = overwrite.pair()
    return _RawOverwrite(allow=allow.value, deny=deny.value)


def should_sync(*, is_public: bool, indexed: bool, visibility_enrolled: bool) -> bool:
    """The one gating predicate used by both backfill and live-event
    handlers to decide whether a channel's content belongs in the mirror.
    is_public is sync-worker-computed (see compute_is_public); indexed and
    visibility_enrolled are both mod-controlled (indexed defaults true on
    first sight; visibility_enrolled defaults false -- see migration
    0011_channel_visibility_enrollment.sql), never mutated by the sync
    worker itself.

    A channel syncs if it's indexed AND either @everyone can already see it
    (is_public) or a mod has deliberately enrolled it into per-user
    visibility filtering (visibility_enrolled) -- the latter is what makes
    Phase 2's "index non-public channels" (DESIGN.md §7) actually possible:
    without it, a role-gated channel's content would never enter Postgres
    at all, no matter how its per-user visibility resolves at read time
    (web/authz.py::resolve_visible_channel_ids). is_public-only content
    still gets the belt-and-suspenders read-time check everywhere
    (db/queries._visibility_clause), same as before.
    """
    return indexed and (is_public or visibility_enrolled)
