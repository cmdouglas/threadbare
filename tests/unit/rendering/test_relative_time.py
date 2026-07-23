from datetime import UTC, datetime, timedelta

from threadbare.rendering.relative_time import relative_time

NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def _ago(**kwargs) -> datetime:
    return NOW - timedelta(**kwargs)


def test_relative_time_just_now_for_a_few_seconds():
    assert relative_time(_ago(seconds=10), now=NOW) == "just now"


def test_relative_time_a_minute_ago_at_one_minute():
    assert relative_time(_ago(minutes=1), now=NOW) == "a minute ago"


def test_relative_time_minutes_ago():
    assert relative_time(_ago(minutes=5), now=NOW) == "5 minutes ago"


def test_relative_time_an_hour_ago_at_one_hour():
    assert relative_time(_ago(hours=1), now=NOW) == "an hour ago"


def test_relative_time_hours_ago():
    assert relative_time(_ago(hours=3), now=NOW) == "3 hours ago"


def test_relative_time_a_day_ago_at_one_day():
    assert relative_time(_ago(days=1), now=NOW) == "a day ago"


def test_relative_time_days_ago():
    assert relative_time(_ago(days=2), now=NOW) == "2 days ago"
    assert relative_time(_ago(days=10), now=NOW) == "10 days ago"


def test_relative_time_a_month_ago_at_one_month():
    assert relative_time(_ago(days=30), now=NOW) == "a month ago"


def test_relative_time_months_ago():
    assert relative_time(_ago(days=60), now=NOW) == "2 months ago"


def test_relative_time_a_year_ago_at_one_year():
    assert relative_time(_ago(days=365), now=NOW) == "a year ago"


def test_relative_time_years_ago():
    assert relative_time(_ago(days=730), now=NOW) == "2 years ago"


def test_relative_time_defaults_now_to_the_current_time():
    assert relative_time(datetime.now(UTC)) == "just now"


def test_relative_time_clamps_a_future_timestamp_to_just_now():
    # Guards against clock skew between the app server and Postgres briefly
    # making a very recent posted_at appear to be in the future.
    assert relative_time(NOW + timedelta(seconds=5), now=NOW) == "just now"
