"""Read-only queries for displaying mirrored content, as opposed to
sync_worker/repository.py which is scoped to the sync worker's own writes.
The forum web app (ROADMAP.md §4) will need many more read-only queries
(board index, pagination, search) that don't belong under sync_worker/
either — this module is where those grow from.
"""

from collections.abc import Iterable

import psycopg


async def get_message_for_render(conn: psycopg.AsyncConnection, message_id: int) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT m.id, m.channel_id, m.thread_id, m.author_id, m.content,
                   m.reply_to_id, m.posted_at, m.edited_at,
                   u.display_name AS author_display_name
            FROM messages m
            JOIN users u ON u.id = m.author_id
            WHERE m.id = %s
            """,
            (message_id,),
        )
        return await cur.fetchone()


async def get_attachments_for_message(
    conn: psycopg.AsyncConnection, message_id: int
) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, filename, content_type, size, cached_url, url_expires_at
            FROM attachments
            WHERE message_id = %s
            ORDER BY id
            """,
            (message_id,),
        )
        return await cur.fetchall()


async def get_embeds_for_message(conn: psycopg.AsyncConnection, message_id: int) -> list[dict]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT position, type, title, description, url, color, author_name,
                   author_url, footer_text, image_url, thumbnail_url, fields
            FROM embeds
            WHERE message_id = %s
            ORDER BY position
            """,
            (message_id,),
        )
        return await cur.fetchall()


async def get_reactions_for_message(
    conn: psycopg.AsyncConnection, message_id: int
) -> list[tuple[str, int]]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT emoji, count FROM reactions WHERE message_id = %s ORDER BY emoji",
            (message_id,),
        )
        rows = await cur.fetchall()
    return [(row["emoji"], row["count"]) for row in rows]


async def resolve_users(conn: psycopg.AsyncConnection, ids: Iterable[int]) -> dict[int, str]:
    ids = list(ids)
    if not ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, display_name FROM users WHERE id = ANY(%s)", (ids,))
        rows = await cur.fetchall()
    return {row["id"]: row["display_name"] for row in rows}


async def resolve_channels(conn: psycopg.AsyncConnection, ids: Iterable[int]) -> dict[int, str]:
    ids = list(ids)
    if not ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, name FROM channels WHERE id = ANY(%s)", (ids,))
        rows = await cur.fetchall()
    return {row["id"]: row["name"] for row in rows}
