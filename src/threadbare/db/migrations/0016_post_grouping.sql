-- Consecutive-post merging (DESIGN.md §5, ROADMAP.md Phase 3): an
-- admin-level, default-off option to render a run of consecutive messages by
-- one author as a single post, with pagination counting posts rather than
-- messages.
--
-- Non-destructive by design. Messages stay 1:1 with Discord and nothing
-- stores group *membership*; each message only records whether it starts a
-- post, and a post is "a head plus every message following it until the next
-- head". That's what keeps maintenance bounded: deleting a head flips one
-- row rather than rewriting a run, and the whole feature stays reversible
-- because turning the toggle off simply stops reading the column.
--
-- DEFAULT true means "every message is its own post" -- today's behaviour
-- exactly -- so this migration needs no data pass and existing installs see
-- no change until a mod opts in. Real values are computed at ingestion from
-- then on, and backfilled for history by a regroup pass (db/grouping.py).
ALTER TABLE messages ADD COLUMN starts_group boolean NOT NULL DEFAULT true;

-- Partial indexes over *heads only*, mirroring the two full
-- messages_{channel,thread}_id_posted_at_idx indexes 0001 already declares.
-- Paginating merged posts means walking heads in (posted_at, id) order, so
-- these keep that scan exactly as cheap as today's message scan -- which is
-- what preserves get_messages_page's nearest-end optimisation (it walks from
-- whichever end of the container is closer, and needs an ordered index at
-- both ends to do it).
CREATE INDEX messages_channel_group_head_idx ON messages (channel_id, posted_at, id)
    WHERE starts_group;
CREATE INDEX messages_thread_group_head_idx ON messages (thread_id, posted_at, id)
    WHERE starts_group;

-- merge_gap_seconds: how long a silence breaks a run. 420 = 7 minutes,
-- Discord's own visual grouping window, so the default matches what a reader
-- already sees in the client.
--
-- grouping_generation: bumped whenever the toggle or the gap changes, which
-- invalidates every stored starts_group. sync_state carries the counterpart
-- per channel; nightly reconciliation regroups any channel whose stamp is
-- behind, so a settings change heals without anyone pressing anything (the
-- admin Regroup button and the CLI are the impatient paths, not the only
-- ones). Starts at -1 rather than 0 so every pre-existing channel reads as
-- stale against site_settings' 0 and gets its first real regroup.
ALTER TABLE site_settings
    ADD COLUMN merge_consecutive_posts boolean NOT NULL DEFAULT false,
    ADD COLUMN merge_gap_seconds integer NOT NULL DEFAULT 420,
    ADD COLUMN grouping_generation integer NOT NULL DEFAULT 0;

ALTER TABLE sync_state ADD COLUMN grouping_generation integer NOT NULL DEFAULT -1;
