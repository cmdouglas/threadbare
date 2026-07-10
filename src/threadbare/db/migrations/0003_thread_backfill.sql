-- Per-thread backfill checkpoints, mirroring sync_state's shape for
-- channels (0001_initial_schema.sql). Kept as a separate table rather than
-- generalizing sync_state to a nullable channel_id/thread_id pair (the
-- pattern messages uses): the two container kinds never need to be queried
-- together (unlike messages, which has real shared-read use cases), and
-- every consumer already knows exactly which kind it has. Matches the
-- existing channels/threads precedent of separate tables per concept.
CREATE TABLE thread_sync_state (
    thread_id bigint PRIMARY KEY REFERENCES threads (id) ON DELETE CASCADE,
    last_backfilled_message_id bigint,
    backfill_complete boolean NOT NULL DEFAULT false,
    last_reconciled_at timestamptz,
    heartbeat_at timestamptz
);
