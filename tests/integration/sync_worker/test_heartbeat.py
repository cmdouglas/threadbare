from datetime import UTC, datetime

from threadbare.sync_worker.heartbeat import beat


async def test_beat_creates_the_singleton_row(db_conn):
    await beat(db_conn)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT updated_at, last_gateway_event_at FROM worker_heartbeat")
        row = await cur.fetchone()
    assert row is not None
    assert row["updated_at"] is not None
    assert row["last_gateway_event_at"] is None


async def test_beat_updates_the_existing_row_not_a_new_one(db_conn):
    await beat(db_conn)
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM worker_heartbeat")
        first_count = (await cur.fetchone())["n"]

    await beat(db_conn)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT count(*) AS n FROM worker_heartbeat")
        second_count = (await cur.fetchone())["n"]
    assert first_count == 1
    assert second_count == 1


async def test_beat_records_last_gateway_event_at(db_conn):
    event_time = datetime(2026, 1, 1, tzinfo=UTC)

    await beat(db_conn, last_gateway_event_at=event_time)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT last_gateway_event_at FROM worker_heartbeat")
        row = await cur.fetchone()
    assert row["last_gateway_event_at"] == event_time


async def test_beat_preserves_last_gateway_event_at_when_not_given(db_conn):
    event_time = datetime(2026, 1, 1, tzinfo=UTC)
    await beat(db_conn, last_gateway_event_at=event_time)

    await beat(db_conn)  # a plain "still alive" beat, no new event

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT last_gateway_event_at FROM worker_heartbeat")
        row = await cur.fetchone()
    assert row["last_gateway_event_at"] == event_time


async def test_beat_updates_updated_at_on_each_call(db_conn):
    await beat(db_conn)
    async with db_conn.cursor() as cur:
        await cur.execute("SELECT updated_at FROM worker_heartbeat")
        first = (await cur.fetchone())["updated_at"]

    await beat(db_conn)

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT updated_at FROM worker_heartbeat")
        second = (await cur.fetchone())["updated_at"]
    assert second >= first
