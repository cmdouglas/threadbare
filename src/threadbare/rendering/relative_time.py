"""Timestamp -> short, human-friendly "N units ago" string. Pure, no I/O.

Thresholds mirror moment.js's well-known humanize() scheme (widely used,
already battle-tested for what "feels right" at each boundary) rather than
inventing our own from scratch. Month/year lengths are fixed averages
(30/365 days), not calendar-aware -- fine for a fuzzy label, and it keeps
this a pure function of two datetimes with no calendar dependency.
"""

from datetime import UTC, datetime

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_MONTH = 30 * _DAY
_YEAR = 365 * _DAY


def relative_time(dt: datetime, *, now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(UTC)
    # Clamps a future timestamp to "just now" rather than a negative
    # duration -- guards against brief clock skew between the app server
    # and Postgres.
    seconds = max((now - dt).total_seconds(), 0)

    if seconds < 45:
        return "just now"
    if seconds < 90:
        return "a minute ago"
    if seconds < 45 * _MINUTE:
        return f"{round(seconds / _MINUTE)} minutes ago"
    if seconds < 90 * _MINUTE:
        return "an hour ago"
    if seconds < 22 * _HOUR:
        return f"{round(seconds / _HOUR)} hours ago"
    if seconds < 36 * _HOUR:
        return "a day ago"
    if seconds < 26 * _DAY:
        return f"{round(seconds / _DAY)} days ago"
    if seconds < 45 * _DAY:
        return "a month ago"
    if seconds < 320 * _DAY:
        return f"{round(seconds / _MONTH)} months ago"
    if seconds < 548 * _DAY:
        return "a year ago"
    return f"{round(seconds / _YEAR)} years ago"
