-- Singleton row (same idiom as worker_heartbeat/wizard_state) for
-- site-wide mod-configurable settings. First setting: whether
-- discovery.discover_channels()'s batch reconnect pass should default a
-- genuinely new channel to indexed=true (today's behavior, and this
-- column's default) or false, mirroring the live CHANNEL_CREATE path's
-- always-false default (ROADMAP.md UI polish backlog). No seed row here --
-- both read paths fall back to true when the row doesn't exist yet, so one
-- only gets created the first time a mod actually flips the toggle.
CREATE TABLE site_settings (
    id boolean PRIMARY KEY DEFAULT true,
    CONSTRAINT site_settings_singleton CHECK (id),
    auto_index_new_channels boolean NOT NULL DEFAULT true
);
