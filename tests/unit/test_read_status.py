from datetime import UTC, datetime

from threadbare.read_status import is_unread

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 2, tzinfo=UTC)


def _aggregate(*, last_message_id, last_posted_at):
    return {"last_message_id": last_message_id, "last_posted_at": last_posted_at}


def _marker(*, last_read_message_id, last_read_posted_at):
    return {
        "last_read_message_id": last_read_message_id,
        "last_read_posted_at": last_read_posted_at,
    }


def test_no_posts_at_all_is_never_unread():
    assert is_unread(_aggregate(last_message_id=None, last_posted_at=None), None) is False


def test_has_posts_and_no_marker_is_unread():
    aggregate = _aggregate(last_message_id=1, last_posted_at=T1)
    assert is_unread(aggregate, None) is True


def test_marker_at_the_latest_post_is_read():
    aggregate = _aggregate(last_message_id=1, last_posted_at=T1)
    marker = _marker(last_read_message_id=1, last_read_posted_at=T1)
    assert is_unread(aggregate, marker) is False


def test_marker_behind_the_latest_post_is_unread():
    aggregate = _aggregate(last_message_id=2, last_posted_at=T2)
    marker = _marker(last_read_message_id=1, last_read_posted_at=T1)
    assert is_unread(aggregate, marker) is True


def test_marker_ahead_of_a_stale_aggregate_is_read():
    # Shouldn't happen in practice (a marker can't outrun real posts) but
    # the comparison should degrade to "not unread" rather than raise.
    aggregate = _aggregate(last_message_id=1, last_posted_at=T1)
    marker = _marker(last_read_message_id=2, last_read_posted_at=T2)
    assert is_unread(aggregate, marker) is False


def test_same_posted_at_breaks_the_tie_on_message_id():
    aggregate = _aggregate(last_message_id=2, last_posted_at=T1)
    marker = _marker(last_read_message_id=1, last_read_posted_at=T1)
    assert is_unread(aggregate, marker) is True
