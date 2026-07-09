from datetime import UTC, datetime

from threadbare.sync_worker.reconciliation import diff_message_sets, next_run_at


def test_diff_message_sets_finds_local_only_ids():
    stale = diff_message_sets(local_ids={1, 2, 3}, remote_ids={2, 3, 4})
    assert stale == {1}


def test_diff_message_sets_empty_when_sets_match():
    assert diff_message_sets(local_ids={1, 2}, remote_ids={1, 2}) == set()


def test_diff_message_sets_all_stale_when_remote_empty():
    assert diff_message_sets(local_ids={1, 2}, remote_ids=set()) == {1, 2}


def test_diff_message_sets_nothing_stale_when_local_empty():
    assert diff_message_sets(local_ids=set(), remote_ids={1, 2}) == set()


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
