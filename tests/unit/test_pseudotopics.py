from datetime import UTC, datetime, timedelta, timezone

from threadbare.pseudotopics import parse_week_id, week_bounds, week_id_for


def test_week_id_for_a_plain_midweek_date():
    assert week_id_for(datetime(2026, 7, 8, 12, tzinfo=UTC)) == "2026-W28"


def test_week_id_for_converts_non_utc_timezones_to_utc():
    # 2026-01-05 00:30 in UTC+1 is still 2025-12-31 23:30 UTC -- ISO week 1
    # of 2026 (year-boundary case below), not week 2.
    plus_one = timezone(timedelta(hours=1))
    dt = datetime(2026, 1, 5, 0, 30, tzinfo=plus_one)

    assert week_id_for(dt) == "2026-W01"


def test_week_id_for_handles_iso_year_boundary():
    # 2025-12-29 is a Monday that ISO-calendar-wise belongs to week 1 of
    # 2026, not week 52/53 of 2025 -- the whole reason to use isocalendar()
    # rather than naive year/week-of-year math.
    assert week_id_for(datetime(2025, 12, 29, tzinfo=UTC)) == "2026-W01"


def test_parse_week_id_round_trips():
    assert parse_week_id("2026-W28") == (2026, 28)


def test_week_bounds_spans_exactly_seven_days():
    start, end = week_bounds("2026-W28")

    assert end - start == timedelta(days=7)


def test_week_bounds_starts_on_monday_utc():
    start, _ = week_bounds("2026-W28")

    assert start == datetime(2026, 7, 6, tzinfo=UTC)
    assert start.weekday() == 0


def test_week_bounds_across_the_iso_year_boundary():
    start, end = week_bounds("2026-W01")

    assert start == datetime(2025, 12, 29, tzinfo=UTC)
    assert end == datetime(2026, 1, 5, tzinfo=UTC)
