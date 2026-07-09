-- A singleton row (not per-channel, unlike sync_state) tracking whether the
-- sync worker process is alive and whether its gateway connection is
-- actually delivering events — DESIGN.md §9: "a heartbeat row the sync
-- worker updates each minute; the web app surfaces staleness on the admin
-- page ... Gateway wedge (connected but silent) is caught by the heartbeat
-- comparing last-event time against Discord activity." The comparison/
-- alerting logic itself belongs to the future admin page, not here — this
-- table just captures the raw data points.
CREATE TABLE worker_heartbeat (
    id boolean PRIMARY KEY DEFAULT true,
    CONSTRAINT worker_heartbeat_singleton CHECK (id),
    updated_at timestamptz NOT NULL,
    last_gateway_event_at timestamptz
);
