from dataclasses import dataclass

from threadbare.sync_worker.reconciliation import reconcile_channel


@dataclass
class FakeMessage:
    id: int
    author: object = None


class FakeFetcher:
    def __init__(self, pages: dict[int | None, list[FakeMessage]]):
        self._pages = pages

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        return self._pages.get(after, [])


class FakeSink:
    def __init__(self, local_ids: set[int]):
        self._local_ids = local_ids
        self.written_ids: list[int] = []
        self.deleted_ids: list[int] | None = None
        self.reconciled_channel_id: int | None = None

    async def write_message(self, message, *, channel_id: int) -> None:
        self.written_ids.append(message.id)

    async def local_message_ids_since(self, channel_id: int, after: int) -> set[int]:
        return self._local_ids

    async def delete_messages(self, message_ids: list[int]) -> None:
        self.deleted_ids = list(message_ids)

    async def mark_reconciled(self, channel_id: int) -> None:
        self.reconciled_channel_id = channel_id


async def test_reconcile_upserts_every_fetched_message():
    fetcher = FakeFetcher({100: [FakeMessage(id=101), FakeMessage(id=102)]})
    sink = FakeSink(local_ids={101, 102})

    result = await reconcile_channel(fetcher, sink, channel_id=10, after=100, batch_size=100)

    assert sink.written_ids == [101, 102]
    assert result.upserted == 2


async def test_reconcile_deletes_local_only_ids_missing_from_the_fetch():
    # 103 exists locally (from the old, un-reconciled state) but Discord no
    # longer returns it — a missed MESSAGE_DELETE gateway event.
    fetcher = FakeFetcher({100: [FakeMessage(id=101), FakeMessage(id=102)]})
    sink = FakeSink(local_ids={101, 102, 103})

    result = await reconcile_channel(fetcher, sink, channel_id=10, after=100, batch_size=100)

    assert sink.deleted_ids == [103]
    assert result.deleted == 1


async def test_reconcile_does_not_delete_when_nothing_is_stale():
    fetcher = FakeFetcher({100: [FakeMessage(id=101)]})
    sink = FakeSink(local_ids={101})

    await reconcile_channel(fetcher, sink, channel_id=10, after=100, batch_size=100)

    assert sink.deleted_ids is None


async def test_reconcile_pages_through_multiple_batches():
    fetcher = FakeFetcher(
        {
            100: [FakeMessage(id=101), FakeMessage(id=102)],
            102: [FakeMessage(id=103)],
        }
    )
    sink = FakeSink(local_ids=set())

    result = await reconcile_channel(fetcher, sink, channel_id=10, after=100, batch_size=2)

    assert sink.written_ids == [101, 102, 103]
    assert result.upserted == 3


async def test_reconcile_marks_the_channel_reconciled():
    fetcher = FakeFetcher({100: []})
    sink = FakeSink(local_ids=set())

    await reconcile_channel(fetcher, sink, channel_id=10, after=100, batch_size=100)

    assert sink.reconciled_channel_id == 10


async def test_reconcile_converges_after_simulated_downtime():
    """The 'kill the worker for an hour, restart' scenario: local state has
    drifted in both directions (one message missed a delete, one missed a
    create/edit) while the worker was down. One reconcile pass fixes both.
    """
    fetcher = FakeFetcher(
        {100: [FakeMessage(id=101), FakeMessage(id=200)]}  # 200 is new since downtime
    )
    sink = FakeSink(local_ids={101, 999})  # 999 was deleted on Discord during downtime

    result = await reconcile_channel(fetcher, sink, channel_id=10, after=100, batch_size=100)

    assert set(sink.written_ids) == {101, 200}
    assert sink.deleted_ids == [999]
    assert result.upserted == 2
    assert result.deleted == 1
