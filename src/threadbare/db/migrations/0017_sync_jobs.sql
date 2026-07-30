-- Mod-triggered maintenance jobs, and the first real IPC between the web app
-- and the sync worker (ROADMAP.md §6 recorded the absence of any as exactly
-- why "trigger re-backfill from the admin page" was deferred; this is that
-- plumbing, serving the new regroup action with the same mechanism).
--
-- A polled table rather than LISTEN/NOTIFY: no persistent listener
-- connection to babysit, no missed-notification edge case on reconnect, and
-- it keeps the "minimal moving parts" line of DESIGN.md §9. The worker claims
-- work on its existing timer with SELECT ... FOR UPDATE SKIP LOCKED, so two
-- workers can never run one job twice.
--
-- A table rather than a flag column, specifically so the admin page can show
-- a job's *status* back to the mod who asked for it. A resync is a full
-- history re-walk -- minutes to hours on a large channel -- and a
-- fire-and-forget boolean cannot distinguish "still running" from "died an
-- hour ago", which is the only question a mod actually has.
CREATE TABLE sync_jobs (
    id bigserial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('regroup', 'resync')),
    -- NULL targets every content channel, matching the CLI's
    -- --reset-all-channels / --regroup-all forms.
    channel_id bigint REFERENCES channels (id) ON DELETE CASCADE,
    -- The requesting mod's Discord user id. Deliberately not a foreign key to
    -- users(id), for the same reason read_markers.user_id isn't (0013): a mod
    -- who has never posted has no users row, and asking for a resync
    -- shouldn't require having posted first.
    requested_by bigint,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    -- Set when a handler raised. A failed job is finished (stamped, never
    -- retried automatically) and keeps its message for the admin page --
    -- silently retrying an expensive re-walk forever is worse than showing a
    -- mod what broke.
    error text
);

-- One outstanding job per kind+target, so a mod mashing the button queues one
-- job rather than fifty. Partial on finished_at IS NULL: the constraint is
-- about *pending* work, and completed history must be free to accumulate.
CREATE UNIQUE INDEX sync_jobs_pending_idx ON sync_jobs (kind, channel_id)
    WHERE finished_at IS NULL;

-- The claim query's access path: oldest unfinished job first.
CREATE INDEX sync_jobs_queue_idx ON sync_jobs (requested_at)
    WHERE finished_at IS NULL;
