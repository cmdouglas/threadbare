"""Raw SQL for the sync worker's data writes. Every function accepts an
already-open connection and never calls commit()/rollback() itself — only
the outermost caller (an event handler, a backfill batch boundary, ...)
manages transaction boundaries. This is also what lets integration tests get
per-test isolation for free via rollback, without truncating tables.
"""

import psycopg
from psycopg.types.json import Json

from threadbare.channel_types import NON_CONTENT_TYPES


async def upsert_guild(conn: psycopg.AsyncConnection, row: dict) -> None:
    await conn.execute(
        """
        INSERT INTO guilds (id, name, icon)
        VALUES (%(id)s, %(name)s, %(icon)s)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, icon = EXCLUDED.icon
        """,
        row,
    )


async def upsert_channel(conn: psycopg.AsyncConnection, row: dict, *, indexed: bool = True) -> None:
    """Insert a channel, or update its metadata if already known. Never
    touches is_public or indexed on conflict — is_public is owned exclusively
    by refresh_channel_public_status, indexed by the admin page (or, for a
    fresh INSERT only, the indexed= param below). On a fresh INSERT,
    is_public takes its schema default (false) and the caller is expected to
    compute it separately (see discovery.discover_channels); indexed takes
    whatever indexed= the caller passes (default True, the schema default's
    value) — discover_channels threads its site-wide auto-index setting
    through here, but only ever affects a genuinely new row, since ON
    CONFLICT never touches it.
    """
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, parent_id, type, name, position, topic, indexed)
        VALUES (
            %(id)s, %(guild_id)s, %(parent_id)s, %(type)s, %(name)s, %(position)s, %(topic)s,
            %(indexed)s
        )
        ON CONFLICT (id) DO UPDATE SET
            parent_id = EXCLUDED.parent_id,
            type = EXCLUDED.type,
            name = EXCLUDED.name,
            position = EXCLUDED.position,
            topic = EXCLUDED.topic
        """,
        {**row, "indexed": indexed},
    )


async def get_auto_index_new_channels(conn: psycopg.AsyncConnection) -> bool:
    """Site-wide setting read by discovery.discover_channels: whether a
    genuinely new channel found on a batch reconnect scan should default to
    indexed=true (this function's own fallback, and the historical
    behavior) or false. Falls back to True when no site_settings row exists
    yet -- see migration 0009's docstring.
    """
    async with conn.cursor() as cur:
        await cur.execute("SELECT auto_index_new_channels FROM site_settings WHERE id = true")
        row = await cur.fetchone()
    return row["auto_index_new_channels"] if row else True


async def insert_new_channel(conn: psycopg.AsyncConnection, row: dict) -> None:
    """Inserts a brand-new channel discovered live (CHANNEL_CREATE), always
    with indexed=false regardless of the table's normal schema-default-true
    INSERT (see upsert_channel above) -- a channel just created on Discord
    needs an explicit mod opt-in via the admin panel's existing
    toggle-indexed control before any of its content is ever fetched.
    is_public is unaffected (computed separately, same as upsert_channel).
    ON CONFLICT DO NOTHING: if the row already exists (e.g. a duplicate
    event, or it was already created via another channel's category
    self-heal), leave whatever indexed value is already there alone rather
    than resetting a mod's prior choice back to false.
    """
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, parent_id, type, name, position, topic, indexed)
        VALUES (
            %(id)s, %(guild_id)s, %(parent_id)s, %(type)s, %(name)s, %(position)s, %(topic)s, false
        )
        ON CONFLICT (id) DO NOTHING
        """,
        row,
    )


async def upsert_thread(conn: psycopg.AsyncConnection, row: dict) -> None:
    """Insert a thread, or update its metadata if already known. parent_channel_id
    and created_at are immutable facts about a thread and are never
    overwritten on conflict, matching upsert_channel's convention.
    """
    await conn.execute(
        """
        INSERT INTO threads (id, parent_channel_id, name, archived, created_at, message_count)
        VALUES (
            %(id)s, %(parent_channel_id)s, %(name)s, %(archived)s, %(created_at)s, %(message_count)s
        )
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            archived = EXCLUDED.archived,
            message_count = EXCLUDED.message_count
        """,
        row,
    )


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
        INSERT INTO users (id, display_name, avatar_hash, is_bot, role_ids)
        VALUES (%(id)s, %(display_name)s, %(avatar_hash)s, %(is_bot)s, %(role_ids)s)
        ON CONFLICT (id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            avatar_hash = EXCLUDED.avatar_hash,
            is_bot = EXCLUDED.is_bot,
            role_ids = EXCLUDED.role_ids
        """,
        row,
    )


async def upsert_role(conn: psycopg.AsyncConnection, row: dict) -> None:
    """Insert a role, or update its mutable fields (name/color/position) if
    already known -- mirrors upsert_channel's shape.
    """
    await conn.execute(
        """
        INSERT INTO roles (id, guild_id, name, color, position)
        VALUES (%(id)s, %(guild_id)s, %(name)s, %(color)s, %(position)s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            color = EXCLUDED.color,
            position = EXCLUDED.position
        """,
        row,
    )


async def delete_role(conn: psycopg.AsyncConnection, role_id: int) -> None:
    await conn.execute("DELETE FROM roles WHERE id = %s", (role_id,))


async def upsert_message(conn: psycopg.AsyncConnection, row: dict) -> None:
    await conn.execute(
        """
        INSERT INTO messages (
            id, channel_id, thread_id, author_id, content, reply_to_id,
            posted_at, edited_at, flags, type
        )
        VALUES (
            %(id)s, %(channel_id)s, %(thread_id)s, %(author_id)s, %(content)s,
            %(reply_to_id)s, %(posted_at)s, %(edited_at)s, %(flags)s, %(type)s
        )
        ON CONFLICT (id) DO UPDATE SET
            content = EXCLUDED.content,
            reply_to_id = EXCLUDED.reply_to_id,
            edited_at = EXCLUDED.edited_at,
            flags = EXCLUDED.flags,
            type = EXCLUDED.type
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


async def sync_message_embeds(
    conn: psycopg.AsyncConnection, message_id: int, embeds: list[dict]
) -> None:
    """Makes the embeds table match `embeds` exactly for this message —
    delete-then-bulk-insert, not per-field upsert, matching
    sync_message_reactions's "re-fetch and overwrite" self-healing shape.
    Unlike reactions, embeds have no stable Discord-side id of their own to
    upsert against (position is a local ordering, not an identity), and
    their count/order can change freely between edits, so replace-all is the
    only shape that's actually correct here.
    """
    await conn.execute("DELETE FROM embeds WHERE message_id = %s", (message_id,))
    for embed in embeds:
        await conn.execute(
            """
            INSERT INTO embeds (
                message_id, position, type, title, description, url, color,
                author_name, author_url, footer_text, image_url, thumbnail_url,
                video_url, fields
            )
            VALUES (
                %(message_id)s, %(position)s, %(type)s, %(title)s, %(description)s,
                %(url)s, %(color)s, %(author_name)s, %(author_url)s, %(footer_text)s,
                %(image_url)s, %(thumbnail_url)s, %(video_url)s, %(fields)s
            )
            """,
            {**embed, "fields": Json(embed["fields"])},
        )


async def get_channel_sync_flags(
    conn: psycopg.AsyncConnection, channel_id: int
) -> tuple[bool, bool] | None:
    """(is_public, indexed) for a known channel, or None if we've never seen
    it (e.g. backfill hasn't run for it yet) — nothing to reconcile then.
    """
    async with conn.cursor() as cur:
        await cur.execute("SELECT is_public, indexed FROM channels WHERE id = %s", (channel_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    return row["is_public"], row["indexed"]


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


async def channel_exists(conn: psycopg.AsyncConnection, channel_id: int) -> bool:
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM channels WHERE id = %s", (channel_id,))
        return await cur.fetchone() is not None


async def get_content_channel_ids(conn: psycopg.AsyncConnection) -> list[int]:
    # Categories and voice/stage-voice channels have no content/checkpoint
    # of their own -- excluding them keeps a "reset every channel" caller's
    # reported count meaningful.
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM channels WHERE type != ALL(%s)", (list(NON_CONTENT_TYPES),)
        )
        return [row["id"] for row in await cur.fetchall()]


async def reset_thread_checkpoints_for_channel(
    conn: psycopg.AsyncConnection, channel_id: int
) -> int:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE thread_sync_state
            SET last_backfilled_message_id = NULL, backfill_complete = false
            WHERE thread_id IN (SELECT id FROM threads WHERE parent_channel_id = %s)
            """,
            (channel_id,),
        )
        return cur.rowcount


async def get_thread_backfill_checkpoint(
    conn: psycopg.AsyncConnection, thread_id: int
) -> int | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT last_backfilled_message_id FROM thread_sync_state WHERE thread_id = %s",
            (thread_id,),
        )
        row = await cur.fetchone()
    return row["last_backfilled_message_id"] if row else None


async def set_thread_backfill_checkpoint(
    conn: psycopg.AsyncConnection,
    thread_id: int,
    *,
    last_message_id: int | None,
    complete: bool,
) -> None:
    await conn.execute(
        """
        INSERT INTO thread_sync_state (thread_id, last_backfilled_message_id, backfill_complete)
        VALUES (%s, %s, %s)
        ON CONFLICT (thread_id) DO UPDATE SET
            last_backfilled_message_id = EXCLUDED.last_backfilled_message_id,
            backfill_complete = EXCLUDED.backfill_complete
        """,
        (thread_id, last_message_id, complete),
    )


async def get_thread_message_ids_since(
    conn: psycopg.AsyncConnection, thread_id: int, after: int
) -> set[int]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM messages WHERE thread_id = %s AND id > %s", (thread_id, after)
        )
        rows = await cur.fetchall()
    return {row["id"] for row in rows}


async def mark_thread_reconciled(conn: psycopg.AsyncConnection, thread_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO thread_sync_state (thread_id, last_reconciled_at)
        VALUES (%s, now())
        ON CONFLICT (thread_id) DO UPDATE SET last_reconciled_at = EXCLUDED.last_reconciled_at
        """,
        (thread_id,),
    )


async def delete_thread(conn: psycopg.AsyncConnection, thread_id: int) -> None:
    """Hard delete — messages/thread_sync_state cascade (ON DELETE CASCADE).
    A no-op if the id is unknown, matching delete_message's convention.
    """
    await conn.execute("DELETE FROM threads WHERE id = %s", (thread_id,))


async def delete_channel(conn: psycopg.AsyncConnection, channel_id: int) -> None:
    """Hard delete — messages/threads/sync_state cascade (ON DELETE
    CASCADE); a child channel's parent_id is set to NULL instead of being
    deleted too (ON DELETE SET NULL), matching Discord's own behavior when
    a category is deleted (its channels survive, just uncategorized). A
    no-op if the id is unknown.
    """
    await conn.execute("DELETE FROM channels WHERE id = %s", (channel_id,))


async def delete_message(conn: psycopg.AsyncConnection, message_id: int) -> None:
    """Hard delete — attachments/reactions cascade. A no-op if the id is
    unknown (e.g. a delete event for a message we never indexed).
    """
    await conn.execute("DELETE FROM messages WHERE id = %s", (message_id,))


async def message_exists(conn: psycopg.AsyncConnection, message_id: int) -> bool:
    """The gate live reaction handlers use before writing: a reaction event
    for a message we never stored (outside reconciliation's lookback, or
    never backfilled) would otherwise raise ForeignKeyViolation on
    reactions.message_id.
    """
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1 FROM messages WHERE id = %s", (message_id,))
        return await cur.fetchone() is not None


async def increment_reaction(conn: psycopg.AsyncConnection, *, message_id: int, emoji: str) -> None:
    await conn.execute(
        """
        INSERT INTO reactions (message_id, emoji, count)
        VALUES (%s, %s, 1)
        ON CONFLICT (message_id, emoji) DO UPDATE SET count = reactions.count + 1
        """,
        (message_id, emoji),
    )


async def decrement_reaction(conn: psycopg.AsyncConnection, *, message_id: int, emoji: str) -> None:
    """Decrements an existing (message_id, emoji) row, deleting it once the
    count would reach zero. A no-op if the row doesn't exist (e.g. a REMOVE
    event for a reaction this instance never saw ADDed, after a gateway
    gap) — matches delete_message's no-op-for-unknown-id convention.

    The delete-if-would-reach-zero-or-below runs first, against the
    pre-decrement count (hence <= 1), so only rows with count >= 2 survive
    to be decremented by the second statement.
    """
    await conn.execute(
        "DELETE FROM reactions WHERE message_id = %s AND emoji = %s AND count <= 1",
        (message_id, emoji),
    )
    await conn.execute(
        "UPDATE reactions SET count = count - 1 WHERE message_id = %s AND emoji = %s",
        (message_id, emoji),
    )


async def clear_reactions(conn: psycopg.AsyncConnection, message_id: int) -> None:
    await conn.execute("DELETE FROM reactions WHERE message_id = %s", (message_id,))


async def clear_reaction_emoji(
    conn: psycopg.AsyncConnection, *, message_id: int, emoji: str
) -> None:
    await conn.execute(
        "DELETE FROM reactions WHERE message_id = %s AND emoji = %s", (message_id, emoji)
    )


async def sync_message_reactions(
    conn: psycopg.AsyncConnection, message_id: int, reactions: list[tuple[str, int]]
) -> None:
    """Makes the reactions table match `reactions` exactly for this message
    — upserts every (emoji, count) pair given, then deletes any existing row
    for this message whose emoji isn't in the given set. The same
    "re-fetch and overwrite" self-healing shape write_message() already uses
    for message content, called on every backfill pass, reconciliation
    sweep, and live create/edit with whatever message.reactions the Message
    object at hand carries. An empty `reactions` list correctly clears every
    existing row for the message (emoji = ANY('{}') is false for all rows,
    so the NOT below matches everything).
    """
    emojis = [emoji for emoji, _ in reactions]
    for emoji, count in reactions:
        await conn.execute(
            """
            INSERT INTO reactions (message_id, emoji, count)
            VALUES (%s, %s, %s)
            ON CONFLICT (message_id, emoji) DO UPDATE SET count = EXCLUDED.count
            """,
            (message_id, emoji, count),
        )
    await conn.execute(
        "DELETE FROM reactions WHERE message_id = %s AND NOT (emoji = ANY(%s))",
        (message_id, emojis),
    )


async def delete_messages(conn: psycopg.AsyncConnection, message_ids: list[int]) -> None:
    await conn.execute("DELETE FROM messages WHERE id = ANY(%s)", (message_ids,))


async def get_message_ids_since(
    conn: psycopg.AsyncConnection, channel_id: int, after: int
) -> set[int]:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM messages WHERE channel_id = %s AND id > %s", (channel_id, after)
        )
        rows = await cur.fetchall()
    return {row["id"] for row in rows}


async def mark_channel_reconciled(conn: psycopg.AsyncConnection, channel_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO sync_state (channel_id, last_reconciled_at)
        VALUES (%s, now())
        ON CONFLICT (channel_id) DO UPDATE SET last_reconciled_at = EXCLUDED.last_reconciled_at
        """,
        (channel_id,),
    )


async def purge_channel_content(conn: psycopg.AsyncConnection, channel_id: int) -> None:
    """Remove everything under a channel — its threads (and their messages)
    and its own top-level messages — without deleting the channel row
    itself. Attachments/reactions cascade from message deletion.
    """
    await conn.execute("DELETE FROM threads WHERE parent_channel_id = %s", (channel_id,))
    await conn.execute("DELETE FROM messages WHERE channel_id = %s", (channel_id,))
