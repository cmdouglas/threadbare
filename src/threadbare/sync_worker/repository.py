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


async def upsert_user(conn: psycopg.AsyncConnection, row: dict) -> None:
    await conn.execute(
        """
        INSERT INTO users (id, display_name, avatar_hash)
        VALUES (%(id)s, %(display_name)s, %(avatar_hash)s)
        ON CONFLICT (id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            avatar_hash = EXCLUDED.avatar_hash
        """,
        row,
    )


async def upsert_message(conn: psycopg.AsyncConnection, row: dict) -> None:
    await conn.execute(
        """
        INSERT INTO messages (
            id, channel_id, thread_id, author_id, content, reply_to_id,
            posted_at, edited_at, flags
        )
        VALUES (
            %(id)s, %(channel_id)s, %(thread_id)s, %(author_id)s, %(content)s,
            %(reply_to_id)s, %(posted_at)s, %(edited_at)s, %(flags)s
        )
        ON CONFLICT (id) DO UPDATE SET
            content = EXCLUDED.content,
            reply_to_id = EXCLUDED.reply_to_id,
            edited_at = EXCLUDED.edited_at,
            flags = EXCLUDED.flags
        """,
        row,
    )


async def upsert_attachment(conn: psycopg.AsyncConnection, row: dict) -> None:
    await conn.execute(
        """
        INSERT INTO attachments (
            id, message_id, filename, content_type, size, cached_url, url_expires_at
        )
        VALUES (
            %(id)s, %(message_id)s, %(filename)s, %(content_type)s, %(size)s,
            %(cached_url)s, %(url_expires_at)s
        )
        ON CONFLICT (id) DO UPDATE SET
            cached_url = EXCLUDED.cached_url,
            url_expires_at = EXCLUDED.url_expires_at
        """,
        row,
    )


async def get_backfill_checkpoint(conn: psycopg.AsyncConnection, channel_id: int) -> int | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT last_backfilled_message_id FROM sync_state WHERE channel_id = %s",
            (channel_id,),
        )
        row = await cur.fetchone()
    return row["last_backfilled_message_id"] if row else None


async def set_backfill_checkpoint(
    conn: psycopg.AsyncConnection,
    channel_id: int,
    *,
    last_message_id: int | None,
    complete: bool,
) -> None:
    await conn.execute(
        """
        INSERT INTO sync_state (channel_id, last_backfilled_message_id, backfill_complete)
        VALUES (%s, %s, %s)
        ON CONFLICT (channel_id) DO UPDATE SET
            last_backfilled_message_id = EXCLUDED.last_backfilled_message_id,
            backfill_complete = EXCLUDED.backfill_complete
        """,
        (channel_id, last_message_id, complete),
    )


async def purge_channel_content(conn: psycopg.AsyncConnection, channel_id: int) -> None:
    """Remove everything under a channel — its threads (and their messages)
    and its own top-level messages — without deleting the channel row
    itself. Attachments/reactions cascade from message deletion.
    """
    await conn.execute("DELETE FROM threads WHERE parent_channel_id = %s", (channel_id,))
    await conn.execute("DELETE FROM messages WHERE channel_id = %s", (channel_id,))
