import asyncio
from datetime import UTC, datetime

import pytest

from threadbare.sync_worker import reconciliation
from threadbare.sync_worker.reconciliation import next_run_at, reconciliation_loop


def test_next_run_at_later_today_when_hour_not_yet_passed():
    now = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    assert next_run_at(now, hour=3) == datetime(2026, 1, 1, 3, 0, tzinfo=UTC)


def test_next_run_at_tomorrow_when_hour_already_passed():
    now = datetime(2026, 1, 1, 5, 0, tzinfo=UTC)
    assert next_run_at(now, hour=3) == datetime(2026, 1, 2, 3, 0, tzinfo=UTC)


def test_next_run_at_tomorrow_when_exactly_at_hour():
    # avoid a zero-second sleep / tight scheduling loop
    now = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
    assert next_run_at(now, hour=3) == datetime(2026, 1, 2, 3, 0, tzinfo=UTC)


async def test_reconciliation_loop_survives_a_failing_sweep(monkeypatch):
    """A raising sweep must not end the loop: bot.py only ever creates the
    reconciliation task when it's None, so a dead task means no nightly
    reconciliation until the process restarts.
    """
    sweeps = 0

    async def failing_then_ok(*args, **kwargs):
        nonlocal sweeps
        sweeps += 1
        if sweeps == 1:
            raise RuntimeError("guild fetch exploded")

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        # Let the loop run exactly twice, then unwind it.
        if len(slept) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(reconciliation, "reconcile_guild", failing_then_ok)
    monkeypatch.setattr(reconciliation.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await reconciliation_loop(object(), object(), guild_id=1)

    # The first sweep raised, the loop still scheduled and ran a second one.
    assert sweeps == 2
    assert len(slept) == 2


async def test_reconciliation_loop_still_honours_cancellation(monkeypatch):
    """CancelledError isn't an Exception subclass, so the new broad handler
    must not swallow shutdown.
    """

    async def cancelled_sweep(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(reconciliation, "reconcile_guild", cancelled_sweep)

    with pytest.raises(asyncio.CancelledError):
        await reconciliation_loop(object(), object(), guild_id=1)
