import discord
import psycopg

from threadbare.discord_permissions import (
    REQUIRED_PERMISSIONS,
    OverwriteLike,
    RawOverwrite,
    compute_is_public,
)
from threadbare.sync_worker import repository
from threadbare.sync_worker.channel_overwrites import sync_channel_overwrites


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
    previously_public = flags.is_public if flags is not None else None
    visibility_enrolled = flags.visibility_enrolled if flags is not None else False

    if previously_public and not is_public and not visibility_enrolled:
        await repository.purge_channel_content(conn, channel_id)

    await repository.set_channel_is_public(conn, channel_id, is_public)
    return is_public


async def refresh_channel_bot_access(
    conn: psycopg.AsyncConnection, *, channel_id: int, bot_permissions: int
) -> bool:
    """Recompute whether the bot's own Discord account can currently read
    a channel -- separate from is_public (@everyone's access) and
    visibility_enrolled (a mod's opt-in to per-member filtering at read
    time). should_sync deciding a channel *should* sync is necessary but
    not sufficient: Discord's own REST API rejects the bot's history calls
    outright (403 Forbidden) if the bot itself lacks View Channel/Read
    Message History there, independent of what should_sync's three inputs
    say. Purely informational -- doesn't gate should_sync itself, since a
    channel regaining bot access should resume syncing automatically on
    the very next backfill/reconciliation pass with no separate toggle.
    admin.html surfaces this with instructions for a mod to act on.

    bot_permissions is the caller's job to resolve (discord.py's own
    `channel.permissions_for(channel.guild.me)` already does full
    resolution -- category/channel overwrites, role grants, admin/owner
    bypass -- so there's no need to duplicate discord_permissions.py's
    hand-rolled resolution for this identity). Returns the newly computed
    value.
    """
    bot_can_read = (bot_permissions & REQUIRED_PERMISSIONS) == REQUIRED_PERMISSIONS
    await repository.set_channel_bot_can_read(conn, channel_id, bot_can_read)
    return bot_can_read


def everyone_overwrite(target: discord.abc.GuildChannel) -> RawOverwrite:
    """Extract the @everyone role's raw allow/deny overwrite ints off a
    live discord.py channel or category object — the adapter that bridges
    real Discord objects into compute_is_public()'s OverwriteLike inputs.
    Shared by events.py (live CHANNEL_UPDATE/role events) and discovery.py
    (initial channel discovery), so it lives here rather than in either.
    """
    overwrite = target.overwrites_for(target.guild.default_role)
    allow, deny = overwrite.pair()
    return RawOverwrite(allow=allow.value, deny=deny.value)


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


async def channel_should_sync(conn: psycopg.AsyncConnection, channel_id: int) -> bool:
    """should_sync for a channel we may or may not have a row for -- the exact
    guard backfill/reconciliation/discovery/event handlers all need before
    touching a channel's content.

    Collapses five byte-identical `flags is None or not should_sync(...)`
    blocks that each re-spelled the same positional tuple unpacking. An unknown
    channel (no row yet) is never synced.
    """
    flags = await repository.get_channel_sync_flags(conn, channel_id)
    if flags is None:
        return False
    return should_sync(
        is_public=flags.is_public,
        indexed=flags.indexed,
        visibility_enrolled=flags.visibility_enrolled,
    )


async def refresh_channel_permission_state(
    conn: psycopg.AsyncConnection,
    channel: discord.abc.GuildChannel,
    *,
    default_role_permissions: int,
    sync_overwrites: bool,
) -> None:
    """Recompute everything permission-related this codebase stores about one
    live channel: is_public (@everyone's access), bot_can_read (the bot's own,
    informational), and -- when sync_overwrites is set -- the stored
    role/member overwrite tables.

    This sequence used to be copy-pasted at three call sites, and the copies had
    already diverged on exactly the argument this function makes explicit.
    sync_overwrites is True for the two paths where a channel's *own* overwrites
    can have changed (discovery.discover_channels, and
    events.handle_channel_permissions_changed via CHANNEL_UPDATE/CHANNEL_CREATE)
    and False for events.handle_role_permissions_changed, which fires when a
    role's own attributes change: that can flip is_public for many channels at
    once without adding or removing a single overwrite row. A deleted role's
    overwrite rows are cleaned up by channel_role_overwrites' ON DELETE CASCADE,
    not here. Passing True there would be harmless but would mean re-writing
    every channel's overwrite tables on every role edit.
    """
    category_overwrite = everyone_overwrite(channel.category) if channel.category else None
    await refresh_channel_public_status(
        conn,
        channel_id=channel.id,
        default_role_permissions=default_role_permissions,
        category_overwrite=category_overwrite,
        channel_overwrite=everyone_overwrite(channel),
    )
    await refresh_channel_bot_access(
        conn,
        channel_id=channel.id,
        bot_permissions=channel.permissions_for(channel.guild.me).value,
    )
    if sync_overwrites:
        await sync_channel_overwrites(conn, channel)
