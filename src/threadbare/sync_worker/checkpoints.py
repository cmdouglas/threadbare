"""Pure cursor/checkpoint math for paginated backfill. Kept separate from
backfill.py's orchestration so it's trivially unit-testable and so the
message-history cursor (snowflake-based, here) stays distinct from the
thread-listing cursor (timestamp-based, a different concept — see the sync
worker plan's risk notes; not yet implemented, added when thread backfill is).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BackfillProgress:
    last_message_id: int | None
    complete: bool


def advance_backfill_progress(
    *, batch_message_ids: list[int], requested_limit: int
) -> BackfillProgress:
    """Given the ids just fetched in one backfill batch and the limit that
    was requested, compute the new checkpoint (the highest id seen) and
    whether backfill is complete (fewer messages came back than requested,
    meaning channel history is exhausted).
    """
    if not batch_message_ids:
        return BackfillProgress(last_message_id=None, complete=True)
    return BackfillProgress(
        last_message_id=max(batch_message_ids),
        complete=len(batch_message_ids) < requested_limit,
    )
