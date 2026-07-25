"""Batches the id lookups collect_referenced_ids() found in a message's raw
content into a ResolvedRefs, one query per id-kind rather than per-mention —
this also means a future paginated board view can batch across a whole page
of messages, not just one, by unioning ReferencedIds before resolving.
"""

import psycopg

from threadbare.db import queries
from threadbare.rendering.markdown import ReferencedIds, ResolvedRefs


async def build_resolved_refs(conn: psycopg.AsyncConnection, ids: ReferencedIds) -> ResolvedRefs:
    users = await queries.resolve_users(conn, ids.user_ids)
    channels = await queries.resolve_channels(conn, ids.channel_ids)
    # collect_referenced_ids has always gathered role_ids, but nothing consumed
    # them until Phase 2's permission mirroring added the `roles` table -- until
    # then every role mention rendered as an inert "@unknown role" placeholder.
    roles = {
        row["id"]: row["name"] for row in await queries.get_roles_by_ids(conn, list(ids.role_ids))
    }
    return ResolvedRefs(users=users, channels=channels, roles=roles)
