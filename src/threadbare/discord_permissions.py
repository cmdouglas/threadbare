"""Pure Discord permission-bit math -- no discord.py, no psycopg, no I/O.
Kept top-level and dependency-free (mirrors pagination.py/urls.py's own
"pure top-level module" convention) specifically so the web app's setup
wizard can reuse compute_is_public()/REQUIRED_PERMISSIONS without dragging
discord.py into the web process -- web/discord_rest.py's own docstring
states that decoupling is a deliberate project invariant, not a preference.

sync_worker/permissions.py re-exports these same names for every existing
sync-worker caller, and keeps its own discord.py/psycopg-touching functions
(refresh_channel_public_status, everyone_overwrite, should_sync) alongside
them -- this module is the pure subset extracted out of it, not a
replacement for it.
"""

from typing import Protocol

# Discord permission bit flags (Discord API docs, PERMISSIONS bitwise flags).
VIEW_CHANNEL = 1 << 10
READ_MESSAGE_HISTORY = 1 << 16

REQUIRED_PERMISSIONS = VIEW_CHANNEL | READ_MESSAGE_HISTORY


class OverwriteLike(Protocol):
    allow: int
    deny: int


def apply_overwrite(permissions: int, overwrite: OverwriteLike | None) -> int:
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
    permissions = apply_overwrite(permissions, category_overwrite)
    permissions = apply_overwrite(permissions, channel_overwrite)
    return (permissions & REQUIRED_PERMISSIONS) == REQUIRED_PERMISSIONS
