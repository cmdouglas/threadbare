-- Admin-registered custom themes (ROADMAP.md Phase 3, DESIGN.md §7). Metadata
-- only: the actual bundle (theme.css + media assets) lives on a filesystem
-- volume (THEME_STORAGE_DIR), extracted from the uploaded .zip -- see
-- web/theme_storage.py. slug is the URL/identifier and must not collide with a
-- built-in theme slug (checked in the web layer, not here). updated_at bumps on
-- re-upload and drives the stylesheet <link>'s ?v= cache-buster. Deliberately
-- NOT captured by the nightly DB backup's media (there is none) -- the themes
-- volume is a separate backup target (DESIGN.md §9); a row whose files are
-- missing degrades to the default theme rather than a broken page.
CREATE TABLE custom_themes (
    slug text PRIMARY KEY,
    display_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
