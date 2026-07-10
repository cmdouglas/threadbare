from dataclasses import dataclass, field

from threadbare.sync_worker.backfill import backfill_channel, backfill_thread


@dataclass
class FakeMessage:
    id: int
    author: object
    content: str = ""
    created_at: object = None
    edited_at: object = None
    reference: object = None
    attachments: list = field(default_factory=list)


class FakeFetcher:
    """Serves fixed pages of messages, keyed by the `after` cursor it was
    called with, so tests can assert exactly which cursor each call used.
    """

    def __init__(self, pages: dict[int | None, list[FakeMessage]]):
        self._pages = pages
        self.calls: list[dict] = []

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls.append({"channel_id": channel_id, "after": after, "limit": limit})
        return self._pages.get(after, [])


class FakeSink:
    def __init__(self, initial_checkpoint: int | None = None):
        self.written_message_ids: list[int] = []
        self._checkpoint = initial_checkpoint
        self._thread_checkpoint = initial_checkpoint
        self.complete: bool | None = None
        self.commit_count = 0

    async def get_checkpoint(self, channel_id: int) -> int | None:
        return self._checkpoint

    async def write_message(
        self, message, *, channel_id: int | None = None, thread_id: int | None = None
    ) -> None:
        self.written_message_ids.append(message.id)

    async def set_checkpoint(self, channel_id: int, *, last_message_id, complete: bool) -> None:
        self._checkpoint = last_message_id
        self.complete = complete

    async def get_thread_checkpoint(self, thread_id: int) -> int | None:
        return self._thread_checkpoint

    async def set_thread_checkpoint(
        self, thread_id: int, *, last_message_id, complete: bool
    ) -> None:
        self._thread_checkpoint = last_message_id
        self.complete = complete

    async def commit(self) -> None:
        self.commit_count += 1


async def test_backfill_starts_from_beginning_when_no_checkpoint():
    author = object()
    page = [FakeMessage(id=1, author=author), FakeMessage(id=2, author=author)]
    fetcher = FakeFetcher({None: page})
    sink = FakeSink(initial_checkpoint=None)

    written = await backfill_channel(fetcher, sink, channel_id=10, batch_size=2)

    assert written == 2
    assert sink.written_message_ids == [1, 2]
    assert fetcher.calls[0]["after"] is None


async def test_backfill_resumes_from_existing_checkpoint():
    author = object()
    fetcher = FakeFetcher({50: [FakeMessage(id=51, author=author)]})
    sink = FakeSink(initial_checkpoint=50)

    await backfill_channel(fetcher, sink, channel_id=10, batch_size=100)

    assert fetcher.calls[0]["after"] == 50
    assert sink.written_message_ids == [51]


async def test_backfill_pages_until_a_partial_batch_signals_exhaustion():
    author = object()
    fetcher = FakeFetcher(
        {
            None: [FakeMessage(id=1, author=author), FakeMessage(id=2, author=author)],
            2: [FakeMessage(id=3, author=author)],  # partial batch (1 < limit 2) -> done
        }
    )
    sink = FakeSink()

    written = await backfill_channel(fetcher, sink, channel_id=10, batch_size=2)

    assert written == 3
    assert len(fetcher.calls) == 2
    assert sink.complete is True
    assert sink._checkpoint == 3


async def test_backfill_marks_complete_and_keeps_checkpoint_on_empty_final_page():
    author = object()
    fetcher = FakeFetcher(
        {
            None: [FakeMessage(id=1, author=author), FakeMessage(id=2, author=author)],
            2: [],  # exactly exhausted on a full-batch boundary
        }
    )
    sink = FakeSink()

    await backfill_channel(fetcher, sink, channel_id=10, batch_size=2)

    assert sink.complete is True
    assert sink._checkpoint == 2  # not clobbered to None by the empty page


async def test_backfill_commits_once_per_batch():
    # Guards against regressing to a single trailing commit: a crash between
    # batches should only lose the in-flight batch, not everything before it.
    author = object()
    fetcher = FakeFetcher(
        {
            None: [FakeMessage(id=1, author=author), FakeMessage(id=2, author=author)],
            2: [FakeMessage(id=3, author=author)],  # partial batch -> done
        }
    )
    sink = FakeSink()

    await backfill_channel(fetcher, sink, channel_id=10, batch_size=2)

    assert sink.commit_count == len(fetcher.calls) == 2


async def test_backfill_thread_starts_from_beginning_when_no_checkpoint():
    author = object()
    page = [FakeMessage(id=1, author=author), FakeMessage(id=2, author=author)]
    fetcher = FakeFetcher({None: page})
    sink = FakeSink(initial_checkpoint=None)

    written = await backfill_thread(fetcher, sink, thread_id=3000, batch_size=2)

    assert written == 2
    assert sink.written_message_ids == [1, 2]
    assert fetcher.calls[0]["after"] is None


async def test_backfill_thread_resumes_from_existing_checkpoint():
    author = object()
    fetcher = FakeFetcher({50: [FakeMessage(id=51, author=author)]})
    sink = FakeSink(initial_checkpoint=50)

    await backfill_thread(fetcher, sink, thread_id=3000, batch_size=100)

    assert fetcher.calls[0]["after"] == 50
    assert sink.written_message_ids == [51]


async def test_backfill_thread_pages_until_a_partial_batch_signals_exhaustion():
    author = object()
    fetcher = FakeFetcher(
        {
            None: [FakeMessage(id=1, author=author), FakeMessage(id=2, author=author)],
            2: [FakeMessage(id=3, author=author)],  # partial batch (1 < limit 2) -> done
        }
    )
    sink = FakeSink()

    written = await backfill_thread(fetcher, sink, thread_id=3000, batch_size=2)

    assert written == 3
    assert len(fetcher.calls) == 2
    assert sink.complete is True
    assert sink._thread_checkpoint == 3


async def test_backfill_thread_marks_complete_and_keeps_checkpoint_on_empty_final_page():
    author = object()
    fetcher = FakeFetcher(
        {
            None: [FakeMessage(id=1, author=author), FakeMessage(id=2, author=author)],
            2: [],  # exactly exhausted on a full-batch boundary
        }
    )
    sink = FakeSink()

    await backfill_thread(fetcher, sink, thread_id=3000, batch_size=2)

    assert sink.complete is True
    assert sink._thread_checkpoint == 2  # not clobbered to None by the empty page


async def test_backfill_thread_commits_once_per_batch():
    author = object()
    fetcher = FakeFetcher(
        {
            None: [FakeMessage(id=1, author=author), FakeMessage(id=2, author=author)],
            2: [FakeMessage(id=3, author=author)],  # partial batch -> done
        }
    )
    sink = FakeSink()

    await backfill_thread(fetcher, sink, thread_id=3000, batch_size=2)

    assert sink.commit_count == len(fetcher.calls) == 2
