from dataclasses import dataclass

from threadbare.sync_worker.reconciliation import reconcile_thread


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
        self.reconciled_thread_id: int | None = None
        self.commit_count = 0

    async def write_message(
        self, message, *, channel_id: int | None = None, thread_id: int | None = None
    ) -> None:
        self.written_ids.append(message.id)

    async def local_thread_message_ids_since(self, thread_id: int, after: int) -> set[int]:
        return self._local_ids

    async def delete_messages(self, message_ids: list[int]) -> None:
        self.deleted_ids = list(message_ids)

    async def mark_thread_reconciled(self, thread_id: int) -> None:
        self.reconciled_thread_id = thread_id

    async def commit(self) -> None:
        self.commit_count += 1


async def test_reconcile_thread_upserts_every_fetched_message():
    fetcher = FakeFetcher({100: [FakeMessage(id=101), FakeMessage(id=102)]})
    sink = FakeSink(local_ids={101, 102})

    result = await reconcile_thread(fetcher, sink, thread_id=3000, after=100, batch_size=100)

    assert sink.written_ids == [101, 102]
    assert result.upserted == 2


async def test_reconcile_thread_deletes_local_only_ids_missing_from_the_fetch():
    fetcher = FakeFetcher({100: [FakeMessage(id=101), FakeMessage(id=102)]})
    sink = FakeSink(local_ids={101, 102, 103})

    result = await reconcile_thread(fetcher, sink, thread_id=3000, after=100, batch_size=100)

    assert sink.deleted_ids == [103]
    assert result.deleted == 1


async def test_reconcile_thread_does_not_delete_when_nothing_is_stale():
    fetcher = FakeFetcher({100: [FakeMessage(id=101)]})
    sink = FakeSink(local_ids={101})

    await reconcile_thread(fetcher, sink, thread_id=3000, after=100, batch_size=100)

    assert sink.deleted_ids is None


async def test_reconcile_thread_pages_through_multiple_batches():
    fetcher = FakeFetcher(
        {
            100: [FakeMessage(id=101), FakeMessage(id=102)],
            102: [FakeMessage(id=103)],
        }
    )
    sink = FakeSink(local_ids=set())

    result = await reconcile_thread(fetcher, sink, thread_id=3000, after=100, batch_size=2)

    assert sink.written_ids == [101, 102, 103]
    assert result.upserted == 3


async def test_reconcile_thread_marks_the_thread_reconciled():
    fetcher = FakeFetcher({100: []})
    sink = FakeSink(local_ids=set())

    await reconcile_thread(fetcher, sink, thread_id=3000, after=100, batch_size=100)

    assert sink.reconciled_thread_id == 3000


async def test_reconcile_thread_converges_after_simulated_downtime():
    """The 'kill the worker for an hour, restart' scenario, for a thread this
    time: local state has drifted in both directions while the worker was
    down. One reconcile pass fixes both.
    """
    fetcher = FakeFetcher(
        {100: [FakeMessage(id=101), FakeMessage(id=200)]}  # 200 is new since downtime
    )
    sink = FakeSink(local_ids={101, 999})  # 999 was deleted on Discord during downtime

    result = await reconcile_thread(fetcher, sink, thread_id=3000, after=100, batch_size=100)

    assert set(sink.written_ids) == {101, 200}
    assert sink.deleted_ids == [999]
    assert result.upserted == 2
    assert result.deleted == 1


async def test_reconcile_thread_commits_once_per_page_plus_once_after_reconcile():
    fetcher = FakeFetcher(
        {
            100: [FakeMessage(id=101), FakeMessage(id=102)],
            102: [FakeMessage(id=103)],
        }
    )
    sink = FakeSink(local_ids=set())

    await reconcile_thread(fetcher, sink, thread_id=3000, after=100, batch_size=2)

    assert sink.commit_count == 3  # 2 page commits + 1 final commit
