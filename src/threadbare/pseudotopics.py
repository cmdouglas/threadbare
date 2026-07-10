"""Weekly pseudo-topic buckets for freeform channels (ROADMAP.md §4,
DESIGN.md §10's "calendar weeks vs. gap detection" open question — resolved
here in favor of calendar weeks: predictable permalinks matter more than
optimal reading grouping for a feature whose whole job is stable linking).

ISO calendar weeks, computed in UTC (no per-guild timezone config in v1, per
DESIGN.md §10). Uses date.isocalendar()/fromisocalendar() rather than naive
year/week-of-year math specifically because ISO weeks can cross a Gregorian
year boundary (e.g. 2025-12-29 belongs to ISO week 1 of 2026).
"""

from datetime import UTC, date, datetime, timedelta


def week_id_for(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.astimezone(UTC).isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def parse_week_id(week_id: str) -> tuple[int, int]:
    year_str, week_str = week_id.split("-W")
    return int(year_str), int(week_str)


def week_bounds(week_id: str) -> tuple[datetime, datetime]:
    year, week = parse_week_id(week_id)
    start = datetime.combine(date.fromisocalendar(year, week, 1), datetime.min.time(), tzinfo=UTC)
    return start, start + timedelta(days=7)
