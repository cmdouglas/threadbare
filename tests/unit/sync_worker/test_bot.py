"""Mostly _log_if_failed -- the client is "thin glue" per bot.py's own
docstring and hard to unit test without a live gateway. _log_if_failed
covers making sure a crash in one of on_ready's fire-and-forget background
loops (backfill/reconciliation/heartbeat/member-role-backfill) is actually
logged, since none of them are covered by discord.py's own on_error (that
only wraps gateway event dispatch).

The on_ready ordering tests below are a narrow exception: they construct a
real ThreadbareClient (constructing one doesn't touch the network, only
.start()/.run() do) and monkeypatch every module-level dependency, so
on_ready() itself can be awaited directly without a live gateway.
"""

import asyncio

import pytest

from threadbare.sync_worker.bot import ThreadbareClient, _log_if_failed


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


class _FakeConnCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


class FakePool:
    def connection(self):
        return _FakeConnCtx()


def _patch_on_ready_dependencies(monkeypatch, *, calls: list[str]):
    async def _record(name):
        calls.append(name)

    async def fake_discover_roles(client, conn, *, guild_id):
        await _record("discover_roles")

    async def fake_discover_channels(client, conn, *, guild_id):
        await _record("discover_channels")
        return []

    async def fake_discover_active_threads(client, conn, *, guild_id):
        await _record("discover_active_threads")
        return []

    async def fake_run_member_role_backfill(client, pool, *, guild_id):
        await _record("member_role_backfill")

    async def fake_forever(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr("threadbare.sync_worker.bot.discover_roles", fake_discover_roles)
    monkeypatch.setattr("threadbare.sync_worker.bot.discover_channels", fake_discover_channels)
    monkeypatch.setattr(
        "threadbare.sync_worker.bot.discover_active_threads", fake_discover_active_threads
    )
    monkeypatch.setattr(
        "threadbare.sync_worker.bot._run_member_role_backfill", fake_run_member_role_backfill
    )
    monkeypatch.setattr("threadbare.sync_worker.bot.backfill_guild", fake_forever)
    monkeypatch.setattr("threadbare.sync_worker.bot.reconciliation_loop", fake_forever)
    monkeypatch.setattr("threadbare.sync_worker.bot.heartbeat_loop", fake_forever)


async def test_on_ready_discovers_roles_before_channels_and_threads(monkeypatch):
    # Regression test for a real FK-ordering bug: discover_channels persists
    # each channel's role-tier overwrites, which FK-reference roles.id, so
    # discover_roles must run first -- a fresh connect/reconnect in the old
    # order would raise a ForeignKeyViolation on the very first channel with
    # a role-tier overwrite.
    calls: list[str] = []
    _patch_on_ready_dependencies(monkeypatch, calls=calls)
    client = ThreadbareClient(guild_id=1, pool=FakePool())

    await client.on_ready()

    assert calls[:3] == ["discover_roles", "discover_channels", "discover_active_threads"]


async def test_on_ready_starts_member_role_backfill_once_not_on_reconnect(monkeypatch):
    calls: list[str] = []
    _patch_on_ready_dependencies(monkeypatch, calls=calls)
    client = ThreadbareClient(guild_id=1, pool=FakePool())

    await client.on_ready()
    await asyncio.sleep(0)  # let the backgrounded task actually run
    task_after_first = client._member_role_backfill_task
    await client.on_ready()  # simulates a gateway reconnect
    await asyncio.sleep(0)

    assert client._member_role_backfill_task is task_after_first
    assert calls.count("member_role_backfill") == 1
