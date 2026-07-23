-- Phase 2 data/plumbing prerequisite (DESIGN.md §7, ROADMAP.md): roles
-- gain their actual Discord permission bitfield, and each channel's
-- per-role and per-member permission overwrites get a durable home for the
-- first time -- previously read transiently off live discord.py objects
-- and discarded immediately after computing one boolean (is_public).
ALTER TABLE roles ADD COLUMN permissions bigint NOT NULL DEFAULT 0;

-- bigint (not integer, unlike roles.color) -- Discord's permission
-- bitfield already exceeds 32 bits.
CREATE TABLE channel_role_overwrites (
    channel_id bigint NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    role_id bigint NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    allow bigint NOT NULL,
    deny bigint NOT NULL,
    PRIMARY KEY (channel_id, role_id)
);

CREATE TABLE channel_member_overwrites (
    channel_id bigint NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    user_id bigint NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    allow bigint NOT NULL,
    deny bigint NOT NULL,
    PRIMARY KEY (channel_id, user_id)
);
