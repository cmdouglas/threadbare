from datetime import UTC, datetime, timedelta

from threadbare.db.admin_queries import HEARTBEAT_STALE_THRESHOLD, is_heartbeat_stale

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def test_is_heartbeat_stale_false_when_recently_updated():
    heartbeat = {"updated_at": NOW - timedelta(seconds=30), "last_gateway_event_at": NOW}

    assert is_heartbeat_stale(heartbeat, now=NOW) is False


def test_is_heartbeat_stale_true_when_older_than_threshold():
    heartbeat = {
        "updated_at": NOW - HEARTBEAT_STALE_THRESHOLD - timedelta(seconds=1),
        "last_gateway_event_at": NOW,
    }

    assert is_heartbeat_stale(heartbeat, now=NOW) is True


def test_is_heartbeat_stale_true_when_heartbeat_row_is_none():
    assert is_heartbeat_stale(None, now=NOW) is True
