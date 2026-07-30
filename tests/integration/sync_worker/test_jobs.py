"""The sync_jobs queue: the first real IPC between the web app and the sync
worker (ROADMAP.md §6 recorded the absence of any as exactly why an
admin-triggered re-backfill was deferred).
"""

from threadbare.sync_worker import jobs


async def _seed_channel(conn, *, channel_id=10, guild_id=1):
    await conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, 'Test Guild') ON CONFLICT DO NOTHING",
        (guild_id,),
    )
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, is_public, indexed) "
        "VALUES (%s, %s, 0, 'general', true, true)",
        (channel_id, guild_id),
    )


async def _enqueue(conn, *, kind="regroup", channel_id=None, requested_by=7):
    return await jobs.enqueue(conn, kind=kind, channel_id=channel_id, requested_by=requested_by)


async def test_enqueue_then_claim_returns_the_job(db_conn):
    await _seed_channel(db_conn)
    await _enqueue(db_conn, channel_id=10)

    claimed = await jobs.claim_next(db_conn)

    assert claimed["kind"] == "regroup"
    assert claimed["channel_id"] == 10
    assert claimed["started_at"] is not None


async def test_claiming_an_empty_queue_returns_none(db_conn):
    assert await jobs.claim_next(db_conn) is None


async def test_a_claimed_job_is_not_claimed_twice(db_conn):
    await _seed_channel(db_conn)
    await _enqueue(db_conn, channel_id=10)

    first = await jobs.claim_next(db_conn)
    second = await jobs.claim_next(db_conn)

    assert first is not None
    assert second is None


async def test_jobs_are_claimed_oldest_first(db_conn):
    await _seed_channel(db_conn)
    await _seed_channel(db_conn, channel_id=11)
    first_id = await _enqueue(db_conn, channel_id=10)
    second_id = await _enqueue(db_conn, channel_id=11)

    assert (await jobs.claim_next(db_conn))["id"] == first_id
    assert (await jobs.claim_next(db_conn))["id"] == second_id


async def test_a_second_pending_job_for_the_same_target_is_absorbed(db_conn):
    """A mod mashing the button queues one job, not fifty. The admin page
    disables the button, but the index is what actually guarantees it -- and
    the second enqueue reports "already queued" as a None rather than raising,
    since a raised constraint error would abort the caller's transaction.
    """
    await _seed_channel(db_conn)
    await _enqueue(db_conn, channel_id=10)

    assert await _enqueue(db_conn, channel_id=10) is None


async def test_a_second_pending_guild_wide_job_is_absorbed_too(db_conn):
    """The NULL target is the case migration 0017's index missed: two NULLs
    are not equal under default UNIQUE semantics, so "Regroup every channel"
    queued a second full pass on every press until 0018 made it NULLS NOT
    DISTINCT.
    """
    await _enqueue(db_conn, channel_id=None)

    assert await _enqueue(db_conn, channel_id=None) is None


async def test_a_new_job_is_allowed_once_the_previous_one_finished(db_conn):
    await _seed_channel(db_conn)
    job_id = await _enqueue(db_conn, channel_id=10)
    await jobs.finish(db_conn, job_id)

    assert await _enqueue(db_conn, channel_id=10) != job_id


async def test_different_kinds_for_one_channel_can_both_be_pending(db_conn):
    await _seed_channel(db_conn)
    await _enqueue(db_conn, kind="regroup", channel_id=10)

    await _enqueue(db_conn, kind="resync", channel_id=10)  # must not raise


async def test_finishing_with_an_error_records_it(db_conn):
    await _seed_channel(db_conn)
    job_id = await _enqueue(db_conn, channel_id=10)
    await jobs.claim_next(db_conn)

    await jobs.finish(db_conn, job_id, error="Missing Access")

    async with db_conn.cursor() as cur:
        await cur.execute("SELECT error, finished_at FROM sync_jobs WHERE id = %s", (job_id,))
        row = await cur.fetchone()
    assert row["error"] == "Missing Access"
    assert row["finished_at"] is not None


async def test_recent_jobs_lists_newest_first_with_channel_names(db_conn):
    await _seed_channel(db_conn)
    await _enqueue(db_conn, kind="regroup", channel_id=10)
    await _enqueue(db_conn, kind="resync", channel_id=None)

    listed = await jobs.recent(db_conn, limit=10)

    assert [row["kind"] for row in listed] == ["resync", "regroup"]
    assert listed[1]["channel_name"] == "general"
    assert listed[0]["channel_name"] is None  # guild-wide


async def test_pending_targets_reports_what_is_already_queued(db_conn):
    """What the admin page disables its buttons from."""
    await _seed_channel(db_conn)
    await _enqueue(db_conn, kind="regroup", channel_id=10)
    await _enqueue(db_conn, kind="resync", channel_id=None)

    assert await jobs.pending_targets(db_conn) == {("regroup", 10), ("resync", None)}


class _FakePool:
    """Hands out the test's own connection, so job handlers run inside the
    rollback fixture's transaction like every other integration test here.
    """

    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


async def test_one_unreadable_channel_does_not_abandon_the_rest_of_a_resync(db_conn):
    """The failure a real run hit: a guild-wide resync met a channel the bot
    can't read (403 Missing Access) and stopped there, so every channel after
    it was silently never resynced. Every other multi-channel loop in the sync
    worker isolates per channel; this one must too.
    """
    await _seed_channel(db_conn, channel_id=10)
    await _seed_channel(db_conn, channel_id=11)
    await _seed_channel(db_conn, channel_id=12)
    walked = []

    async def backfill(channel_id):
        if channel_id == 11:
            raise PermissionError("403 Forbidden (error code: 50001): Missing Access")
        walked.append(channel_id)

    error = await jobs.run_one(
        _FakePool(db_conn), {"kind": "resync", "channel_id": None}, backfill=backfill
    )

    assert walked == [10, 12]  # the readable channels still got resynced
    assert error is not None


async def test_a_partial_failure_names_the_channels_that_failed(db_conn):
    """ "403 Missing Access" alone doesn't say *which* channel to go fix, which
    is the whole reason a mod is looking at the job list.
    """
    await _seed_channel(db_conn, channel_id=10)
    await _seed_channel(db_conn, channel_id=11)
    await db_conn.execute("UPDATE channels SET name = 'secret-plans' WHERE id = 11")

    async def backfill(channel_id):
        if channel_id == 11:
            raise PermissionError("403 Forbidden (error code: 50001): Missing Access")

    error = await jobs.run_one(
        _FakePool(db_conn), {"kind": "resync", "channel_id": None}, backfill=backfill
    )

    assert "secret-plans" in error
    assert "Missing Access" in error
    assert "1 of 2" in error


async def test_a_fully_successful_job_records_no_error(db_conn):
    await _seed_channel(db_conn, channel_id=10)

    async def backfill(channel_id):
        return None

    error = await jobs.run_one(
        _FakePool(db_conn), {"kind": "resync", "channel_id": None}, backfill=backfill
    )

    assert error is None


async def test_a_single_channel_resync_still_reports_its_own_failure(db_conn):
    await _seed_channel(db_conn, channel_id=10)

    async def backfill(channel_id):
        raise PermissionError("403 Forbidden (error code: 50001): Missing Access")

    error = await jobs.run_one(
        _FakePool(db_conn), {"kind": "resync", "channel_id": 10}, backfill=backfill
    )

    assert "general" in error
    assert "1 of 1" in error


async def test_one_failing_channel_does_not_abandon_the_rest_of_a_regroup(db_conn):
    """Same isolation for regroup. Exercised by pointing it at a channel id
    that no longer exists, which is a real race: a channel deleted between
    queueing the job and running it.
    """
    await _seed_channel(db_conn, channel_id=10)

    error = await jobs.run_one(
        _FakePool(db_conn), {"kind": "regroup", "channel_id": None}, backfill=None
    )

    assert error is None


async def test_an_exception_with_no_message_still_names_its_type(db_conn):
    """Some exception types stringify to "", which would render as a bare
    "#general: " and read as a truncated message rather than something a mod
    can search for.
    """
    await _seed_channel(db_conn, channel_id=10)

    async def backfill(channel_id):
        raise TimeoutError()

    error = await jobs.run_one(
        _FakePool(db_conn), {"kind": "resync", "channel_id": 10}, backfill=backfill
    )

    assert "#general: TimeoutError" in error
