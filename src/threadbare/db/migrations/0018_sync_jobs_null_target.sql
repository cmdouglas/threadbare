-- Fixes the "one outstanding job per kind+target" guarantee 0017 intended but
-- did not actually get for guild-wide jobs.
--
-- 0017's index is UNIQUE (kind, channel_id) WHERE finished_at IS NULL, and a
-- guild-wide job stores channel_id = NULL. Under default UNIQUE semantics two
-- NULLs are never equal, so ("regroup", NULL) never conflicts with itself --
-- a mod pressing "Regroup every channel" twice queued two full passes, which
-- is exactly what the index was there to prevent. Per-channel jobs were
-- constrained correctly the whole time; only the NULL target leaked.
--
-- NULLS NOT DISTINCT (Postgres 15+, and the compose stack pins 16) makes the
-- two NULLs conflict. The alternative -- a sentinel channel_id of 0 for "every
-- channel" -- would have meant a fake channel id leaking into every query and
-- template that reads this table.
--
-- 0017 is not edited: db/migrate.py checksums applied migrations and refuses
-- to run when one changes, so a shipped migration is immutable even when it's
-- wrong. Corrections come as new migrations.
DROP INDEX sync_jobs_pending_idx;

CREATE UNIQUE INDEX sync_jobs_pending_idx ON sync_jobs (kind, channel_id) NULLS NOT DISTINCT
    WHERE finished_at IS NULL;
