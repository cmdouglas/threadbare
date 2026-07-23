-- Discord's embed.video (the actual animated/transcoded content behind a
-- gifv/video-type embed, e.g. a Tenor/Giphy unfurl) was never captured --
-- embed_to_row only read image/thumbnail. image/thumbnail on those embeds
-- are often just a static preview frame, so rendering only them showed a
-- still image where Discord itself shows an animated clip.

ALTER TABLE embeds ADD COLUMN video_url text;
