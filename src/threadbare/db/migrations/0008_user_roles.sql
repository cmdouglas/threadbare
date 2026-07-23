-- Show each poster's Discord role (colored username) and bot/human status
-- (a badge), matching Discord's own client -- neither was ever captured:
-- users had no is_bot column and no roles table existed at all.

ALTER TABLE users ADD COLUMN is_bot boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN role_ids bigint[] NOT NULL DEFAULT '{}';

-- A plain array of role ids per member (not a join table) -- the only
-- query need is "this member's current role-id list", never "which
-- members hold role X".
CREATE TABLE roles (
    id bigint PRIMARY KEY,
    guild_id bigint NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
    name text NOT NULL,
    color integer NOT NULL,
    position integer NOT NULL
);
CREATE INDEX roles_guild_id_idx ON roles (guild_id);
