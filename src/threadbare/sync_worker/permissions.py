import psycopg

from threadbare.sync_worker import repository
from threadbare.sync_worker.discord_types import OverwriteLike

# Discord permission bit flags (Discord API docs, PERMISSIONS bitwise flags).
VIEW_CHANNEL = 1 << 10
READ_MESSAGE_HISTORY = 1 << 16

REQUIRED_PERMISSIONS = VIEW_CHANNEL | READ_MESSAGE_HISTORY


def _apply_overwrite(permissions: int, overwrite: OverwriteLike | None) -> int:
    if overwrite is None:
        return permissions
    return (permissions & ~overwrite.deny) | overwrite.allow


def compute_is_public(
    default_role_permissions: int,
    category_overwrite: OverwriteLike | None,
    channel_overwrite: OverwriteLike | None,
) -> bool:
    """Whether @everyone can view the channel and read its history.

    Resolves Discord's overwrite precedence for the @everyone role: guild
    base permissions -> category @everyone overwrite -> channel @everyone
    overwrite. The channel overwrite is applied last, so a bit set there
    always wins over the same bit set at the category level.
    """
    permissions = default_role_permissions
    permissions = _apply_overwrite(permissions, category_overwrite)
    permissions = _apply_overwrite(permissions, channel_overwrite)
    return (permissions & REQUIRED_PERMISSIONS) == REQUIRED_PERMISSIONS


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
    stops being @everyone-readable must lose its indexed content). Returns
    the newly computed is_public value.
    """
    is_public = compute_is_public(default_role_permissions, category_overwrite, channel_overwrite)
    previously_public = await repository.get_channel_is_public(conn, channel_id)

    if previously_public and not is_public:
        await repository.purge_channel_content(conn, channel_id)

    await repository.set_channel_is_public(conn, channel_id, is_public)
    return is_public
