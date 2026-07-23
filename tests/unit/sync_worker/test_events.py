"""Unit coverage for events.py's pure logic. Everything else in this module
is thin DB-writing glue (covered by tests/integration/sync_worker/test_events.py
against a real Postgres) -- handle_member_update's before/after diff-and-guard
is the one piece worth isolating without a DB round trip.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock

from threadbare.sync_worker import events


@dataclass
class FakeAsset:
    key: str


@dataclass
class FakeMember:
    id: int
    display_name: str = "someone"
    avatar: FakeAsset | None = None


async def test_handle_member_update_upserts_when_display_name_changed(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(events.repository, "upsert_user", upsert)
    conn = object()
    before = FakeMember(id=1, display_name="old-nick")
    after = FakeMember(id=1, display_name="new-nick")

    await events.handle_member_update(conn, before, after)

    upsert.assert_awaited_once_with(
        conn, {"id": 1, "display_name": "new-nick", "avatar_hash": None}
    )


async def test_handle_member_update_upserts_when_only_avatar_changed(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(events.repository, "upsert_user", upsert)
    conn = object()
    before = FakeMember(id=1, display_name="same-nick", avatar=FakeAsset(key="old"))
    after = FakeMember(id=1, display_name="same-nick", avatar=FakeAsset(key="new"))

    await events.handle_member_update(conn, before, after)

    upsert.assert_awaited_once_with(
        conn, {"id": 1, "display_name": "same-nick", "avatar_hash": "new"}
    )


async def test_handle_member_update_is_a_no_op_when_nothing_relevant_changed(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(events.repository, "upsert_user", upsert)
    member = FakeMember(id=1, display_name="same-nick", avatar=FakeAsset(key="abc"))

    await events.handle_member_update(object(), member, member)

    upsert.assert_not_awaited()
