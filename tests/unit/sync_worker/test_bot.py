"""Only _log_if_failed, not ThreadbareClient/on_ready itself -- the client is
"thin glue" per bot.py's own docstring and hard to unit test without a live
gateway (no test file exists for it today). This covers the one piece of
real logic on_ready wires in: making sure a crash in one of its three
fire-and-forget background loops (backfill/reconciliation/heartbeat) is
actually logged, since none of them are covered by discord.py's own
on_error (that only wraps gateway event dispatch).
"""

import asyncio

import pytest

from threadbare.sync_worker.bot import _log_if_failed


async def test_log_if_failed_logs_error_with_traceback_when_task_raised(caplog):
    async def boom():
        raise ValueError("kaboom")

    task = asyncio.create_task(boom())
    with pytest.raises(ValueError):
        await task

    with caplog.at_level("ERROR"):
        _log_if_failed(task, name="backfill")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "ERROR"
    assert "backfill" in record.message
    assert record.exc_info is not None
    assert record.exc_info[1].args == ("kaboom",)


async def test_log_if_failed_logs_nothing_when_task_succeeded(caplog):
    async def fine():
        return "ok"

    task = asyncio.create_task(fine())
    await task

    with caplog.at_level("ERROR"):
        _log_if_failed(task, name="reconciliation")

    assert caplog.records == []


async def test_log_if_failed_logs_nothing_when_task_was_cancelled(caplog):
    async def forever():
        await asyncio.sleep(3600)

    task = asyncio.create_task(forever())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with caplog.at_level("ERROR"):
        _log_if_failed(task, name="heartbeat")

    assert caplog.records == []
