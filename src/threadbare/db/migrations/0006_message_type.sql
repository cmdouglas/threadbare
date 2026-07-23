-- Captures discord.py's Message.type (a MessageType enum, stored as its raw
-- int value) so system messages (member joins, boosts, pin notices, ...)
-- can be rendered with real text instead of a blank post -- previously
-- never read anywhere in the sync worker. NOT NULL DEFAULT 0 (==
-- MessageType.default, an ordinary user-authored message) so every existing
-- row, and every raw-SQL-seeded test fixture across the suite that doesn't
-- set this column, keeps its current (correct) rendering with no backfill
-- required.
ALTER TABLE messages ADD COLUMN type smallint NOT NULL DEFAULT 0;
