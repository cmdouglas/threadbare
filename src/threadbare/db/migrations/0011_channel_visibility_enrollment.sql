-- Phase 2 (DESIGN.md §7, ROADMAP.md): the per-channel opt-in flag that
-- gates the requester-visibility filtering added in the same step --
-- defaults to false so no existing non-public channel starts being shown
-- to anyone new on upgrade (DESIGN.md's upgrade contract). Mods enroll a
-- channel deliberately from the admin page; enrollment is never automatic
-- (mirrors the existing per-channel `indexed` toggle's shape, migration
-- 0001_initial_schema.sql).
ALTER TABLE channels ADD COLUMN visibility_enrolled boolean NOT NULL DEFAULT false;
