"""Worker-alive heartbeat (DESIGN.md §9) — a singleton row updated on an
interval, distinct from sync_state's per-channel checkpoints. The
comparison/alerting logic for staleness belongs to the future admin page;
this module only records the raw timestamps.
"""

import asyncio
from datetime import datetime, timedelta

import psycopg

DEFAULT_HEARTBEAT_INTERVAL = timedelta(minutes=1)


async def beat(
    conn: psycopg.AsyncConnection, *, last_gateway_event_at: datetime | None = None
) -> None:
    """Update the heartbeat's updated_at to now. If last_gateway_event_at is
    given, records it; otherwise leaves the previously recorded value alone
    (a plain "still alive" beat shouldn't erase real gateway-activity data).
    """
    await conn.execute(
        """
        INSERT INTO worker_heartbeat (id, updated_at, last_gateway_event_at)
        VALUES (true, now(), %(last_gateway_event_at)s)
        ON CONFLICT (id) DO UPDATE SET
            updated_at = EXCLUDED.updated_at,
            last_gateway_event_at = COALESCE(
                %(last_gateway_event_at)s, worker_heartbeat.last_gateway_event_at
            )
        """,
        {"last_gateway_event_at": last_gateway_event_at},
    )


async def heartbeat_loop(
    pool, *, get_last_gateway_event_at, interval: timedelta = DEFAULT_HEARTBEAT_INTERVAL
) -> None:
    """Runs forever, beating on `interval`. get_last_gateway_event_at is a
    zero-arg callable (typically reading an in-memory attribute the client
    updates on every gateway dispatch) rather than a plain value, since the
    loop reads it fresh on every iteration.
    """
    while True:
        async with pool.connection() as conn:
            await beat(conn, last_gateway_event_at=get_last_gateway_event_at())
        await asyncio.sleep(interval.total_seconds())
