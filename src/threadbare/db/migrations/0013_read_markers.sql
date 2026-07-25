-- Per-user last-read position per board/topic (DESIGN.md §7 Phase 3: unread
-- tracking and "first unread post" jumping). Same channel_id/thread_id
-- mutual-exclusion shape as messages itself (messages_container_check),
-- since a marker always belongs to exactly one container.
--
-- user_id is deliberately not a foreign key to users(id): a logged-in
-- member who has never posted (or been swept up by the role backfill) has
-- no users row yet -- the same population gap web/authz.py already
-- tolerates for role_ids -- and reading progress shouldn't require having
-- posted first.
--
-- last_read_message_id/last_read_posted_at together are the same
-- (posted_at, id) ordering key every other query already sorts and
-- compares on (messages_channel_id_posted_at_idx etc.), rather than a bare
-- timestamp or a raw snowflake id alone.
CREATE TABLE read_markers (
    user_id bigint NOT NULL,
    channel_id bigint REFERENCES channels (id) ON DELETE CASCADE,
    thread_id bigint REFERENCES threads (id) ON DELETE CASCADE,
    last_read_message_id bigint NOT NULL,
    last_read_posted_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT read_markers_container_check CHECK (num_nonnulls(channel_id, thread_id) = 1)
);

-- Partial unique indexes (rather than one composite unique constraint over
-- both nullable columns) so ON CONFLICT can target "this user's marker for
-- this channel" and "this user's marker for this thread" independently --
-- a plain UNIQUE (user_id, channel_id, thread_id) would let NULLs multiply
-- rather than conflict.
CREATE UNIQUE INDEX read_markers_user_channel_idx ON read_markers (user_id, channel_id)
    WHERE channel_id IS NOT NULL;
CREATE UNIQUE INDEX read_markers_user_thread_idx ON read_markers (user_id, thread_id)
    WHERE thread_id IS NOT NULL;
