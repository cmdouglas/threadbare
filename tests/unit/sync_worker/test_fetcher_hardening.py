import asyncio

import discord
import pytest

from threadbare.sync_worker.backfill import BoundedHistoryFetcher, RetryingHistoryFetcher


class ConcurrencyTrackingFetcher:
    def __init__(self, delay: float = 0.01):
        self.delay = delay
        self.current = 0
        self.max_seen = 0

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.current += 1
        self.max_seen = max(self.max_seen, self.current)
        await asyncio.sleep(self.delay)
        self.current -= 1
        return []


async def test_bounded_fetcher_caps_concurrency():
    inner = ConcurrencyTrackingFetcher()
    bounded = BoundedHistoryFetcher(inner, max_concurrency=2)

    await asyncio.gather(
        *[bounded.fetch_batch(channel_id=i, after=None, limit=10) for i in range(10)]
    )

    assert inner.max_seen <= 2


async def test_bounded_fetcher_allows_up_to_the_cap_concurrently():
    inner = ConcurrencyTrackingFetcher(delay=0.05)
    bounded = BoundedHistoryFetcher(inner, max_concurrency=4)

    await asyncio.gather(
        *[bounded.fetch_batch(channel_id=i, after=None, limit=10) for i in range(4)]
    )

    assert inner.max_seen == 4


class FlakyFetcher:
    def __init__(self, fail_times: int, retry_after: float = 0.001):
        self.fail_times = fail_times
        self.retry_after = retry_after
        self.calls = 0

    async def fetch_batch(self, *, channel_id: int, after: int | None, limit: int) -> list:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise discord.RateLimited(self.retry_after)
        return ["ok"]


async def test_retrying_fetcher_succeeds_after_transient_rate_limits():
    inner = FlakyFetcher(fail_times=2)
    retrying = RetryingHistoryFetcher(inner, max_retries=3)

    result = await retrying.fetch_batch(channel_id=1, after=None, limit=10)

    assert result == ["ok"]
    assert inner.calls == 3


async def test_retrying_fetcher_gives_up_after_max_retries():
    inner = FlakyFetcher(fail_times=10)
    retrying = RetryingHistoryFetcher(inner, max_retries=2)

    with pytest.raises(discord.RateLimited):
        await retrying.fetch_batch(channel_id=1, after=None, limit=10)

    assert inner.calls == 3  # initial attempt + 2 retries


async def test_retrying_fetcher_does_not_retry_other_errors():
    class BoomFetcher:
        calls = 0

        async def fetch_batch(self, *, channel_id, after, limit):
            self.calls += 1
            raise ValueError("boom")

    inner = BoomFetcher()
    retrying = RetryingHistoryFetcher(inner, max_retries=3)

    with pytest.raises(ValueError):
        await retrying.fetch_batch(channel_id=1, after=None, limit=10)

    assert inner.calls == 1
