-- Discord embeds (link previews, rich content) were never captured by the
-- sync worker before this migration — discord.Message.embeds was simply
-- never read. Replace-all-on-write (see repository.sync_message_embeds),
-- not per-field upsert: embeds have no stable Discord-side id of their own,
-- and their count/order can change on edit, so id is a local bigserial
-- surrogate rather than anything from Discord.

CREATE TABLE embeds (
    id bigserial PRIMARY KEY,
    message_id bigint NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    position smallint NOT NULL DEFAULT 0,
    type text,
    title text,
    description text,
    url text,
    color integer,
    author_name text,
    author_url text,
    footer_text text,
    image_url text,
    thumbnail_url text,
    fields jsonb NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (message_id, position)
);

CREATE INDEX embeds_message_id_idx ON embeds (message_id);
