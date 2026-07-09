"""Raw SQL for the sync worker's data writes. Every function accepts an
already-open connection and never calls commit()/rollback() itself — only
the outermost caller (an event handler, a backfill batch boundary, ...)
manages transaction boundaries. This is also what lets integration tests get
per-test isolation for free via rollback, without truncating tables.
"""

import psycopg


async def get_channel_is_public(conn: psycopg.AsyncConnection, channel_id: int) -> bool | None:
    async with conn.cursor() as cur:
        await cur.execute("SELECT is_public FROM channels WHERE id = %s", (channel_id,))
        row = await cur.fetchone()
    return row["is_public"] if row else None


async def set_channel_is_public(
    conn: psycopg.AsyncConnection, channel_id: int, is_public: bool
) -> None:
    await conn.execute("UPDATE channels SET is_public = %s WHERE id = %s", (is_public, channel_id))


async def purge_channel_content(conn: psycopg.AsyncConnection, channel_id: int) -> None:
    """Remove everything under a channel — its threads (and their messages)
    and its own top-level messages — without deleting the channel row
    itself. Attachments/reactions cascade from message deletion.
    """
    await conn.execute("DELETE FROM threads WHERE parent_channel_id = %s", (channel_id,))
    await conn.execute("DELETE FROM messages WHERE channel_id = %s", (channel_id,))
