-- Drops three columns that were written-but-never-read or never touched at
-- all. Found by a code-quality audit pass, not by a bug.
--
-- messages.flags (0001_initial_schema.sql): written as the literal 0 by
-- transform.message_to_row on every insert *and* re-asserted by
-- upsert_message's `flags = EXCLUDED.flags` on every conflict, which made it
-- look like real self-healing metadata. It was never selected anywhere --
-- absent from queries._MESSAGE_COLUMNS_SQL, from every other SELECT, and from
-- every template. The message metadata that actually got used took a
-- different route later (0006_message_type.sql's `type`, which is read and
-- rendered).
--
-- sync_state.heartbeat_at (0001) and thread_sync_state.heartbeat_at (0003):
-- zero references in src/ or tests/ -- only the two DDL lines that created
-- them. 0002_worker_heartbeat.sql replaced the per-channel heartbeat concept
-- with a singleton worker_heartbeat row (DESIGN.md §9's heartbeat is
-- worker-global), and 0003 then copy-pasted the already-dead column into its
-- new table while "mirroring sync_state's shape".
--
-- DELIBERATE DEPARTURE from DESIGN.md §7's upgrade contract, recorded there
-- rather than left implicit: rule 1 says migrations are additive-only, and
-- rule 2's "rollback is just redeploy the previous image" guarantee depends on
-- it -- the previous image still writes `flags`, so rolling back across this
-- migration would fail on insert. Accepted because no release has been tagged
-- yet (§7's release convention notes as much), so no operator can be mid-
-- upgrade. If a version had shipped, this would have to be an expand/contract
-- pair: stop writing in release N, drop in N+1.

ALTER TABLE messages DROP COLUMN flags;
ALTER TABLE sync_state DROP COLUMN heartbeat_at;
ALTER TABLE thread_sync_state DROP COLUMN heartbeat_at;
