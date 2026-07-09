-- Core schema for the Discord mirror (DESIGN.md §4.1), with two deliberate
-- deviations:
--
-- 1. messages.channel_or_thread_id is split into two nullable FKs,
--    channel_id and thread_id, each ON DELETE CASCADE. This gives real
--    Postgres-level cascading hard-delete (drop a channel/thread row and
--    everything under it disappears in one statement), which is the
--    compliance-critical behavior DESIGN.md §3 calls "deletion honoring."
--
-- 2. messages.posted_at is stored explicitly rather than derived from the
--    snowflake id at query time, per DESIGN.md §7 Phase 6's near-free hedge:
--    "don't let Discord snowflake semantics leak beyond the ingestion layer
--    — store ... an explicit (posted_at, sequence) sort key rather than
--    sorting on snowflake math in queries." id remains the tiebreaker.

CREATE TABLE guilds (
    id bigint PRIMARY KEY,
    name text NOT NULL,
    icon text
);

CREATE TABLE channels (
    id bigint PRIMARY KEY,
    guild_id bigint NOT NULL REFERENCES guilds (id) ON DELETE CASCADE,
    parent_id bigint REFERENCES channels (id) ON DELETE SET NULL,
    type smallint NOT NULL,
    name text NOT NULL,
    position integer NOT NULL DEFAULT 0,
    topic text,
    -- Computed by the sync worker from role/channel permission overwrites;
    -- never set by anything else.
    is_public boolean NOT NULL DEFAULT false,
    -- Mod-controlled (future admin page). Sync worker defaults it true on
    -- first sight of a public channel and never mutates it afterward.
    indexed boolean NOT NULL DEFAULT true
);

CREATE INDEX channels_guild_id_idx ON channels (guild_id);
CREATE INDEX channels_parent_id_idx ON channels (parent_id);

CREATE TABLE threads (
    id bigint PRIMARY KEY,
    -- Threads have no permission overwrites of their own — visibility and
    -- indexing always key off the parent channel's is_public/indexed.
    parent_channel_id bigint NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    name text NOT NULL,
    archived boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL,
    message_count integer NOT NULL DEFAULT 0
);

CREATE INDEX threads_parent_channel_id_idx ON threads (parent_channel_id);

CREATE TABLE users (
    id bigint PRIMARY KEY,
    display_name text NOT NULL,
    avatar_hash text
);

CREATE TABLE messages (
    id bigint PRIMARY KEY,
    channel_id bigint REFERENCES channels (id) ON DELETE CASCADE,
    thread_id bigint REFERENCES threads (id) ON DELETE CASCADE,
    author_id bigint NOT NULL REFERENCES users (id),
    content text NOT NULL DEFAULT '',
    reply_to_id bigint REFERENCES messages (id) ON DELETE SET NULL,
    posted_at timestamptz NOT NULL,
    edited_at timestamptz,
    flags integer NOT NULL DEFAULT 0,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    CONSTRAINT messages_container_check CHECK (num_nonnulls(channel_id, thread_id) = 1)
);

CREATE INDEX messages_channel_id_posted_at_idx ON messages (channel_id, posted_at, id);
CREATE INDEX messages_thread_id_posted_at_idx ON messages (thread_id, posted_at, id);
CREATE INDEX messages_author_id_idx ON messages (author_id);
CREATE INDEX messages_tsv_idx ON messages USING GIN (tsv);

CREATE TABLE attachments (
    id bigint PRIMARY KEY,
    message_id bigint NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    filename text NOT NULL,
    content_type text,
    size bigint NOT NULL,
    -- Signed CDN URL cache + expiry. Refreshing an expired URL is the web
    -- app's /att/{id} proxy job, not the sync worker's — see DESIGN.md §4.
    cached_url text NOT NULL,
    url_expires_at timestamptz NOT NULL
);

CREATE INDEX attachments_message_id_idx ON attachments (message_id);

CREATE TABLE reactions (
    -- Aggregate only, per DESIGN.md §3 requirement 4 — no per-reactor
    -- identity is ever stored.
    message_id bigint NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    emoji text NOT NULL,
    count integer NOT NULL,
    PRIMARY KEY (message_id, emoji)
);

CREATE TABLE sync_state (
    channel_id bigint PRIMARY KEY REFERENCES channels (id) ON DELETE CASCADE,
    last_backfilled_message_id bigint,
    backfill_complete boolean NOT NULL DEFAULT false,
    last_reconciled_at timestamptz,
    heartbeat_at timestamptz
);
