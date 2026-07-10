-- Singleton row tracking first-run setup wizard progress (ROADMAP.md §7,
-- DESIGN.md §8), so an abandoned wizard resumes at the last incomplete step
-- rather than restarting. Deliberately stores NO secrets (bot token, OAuth
-- client secret) -- those live only in the wizard's Flask session between
-- steps and are written directly to .env on completion (config.py's
-- load_settings() is the only thing that reads them back). Losing session
-- state (e.g. a web-process restart mid-wizard, which invalidates the
-- ephemeral per-process session key -- see web/wizard_app.py) means
-- re-entering the token/secret, but not redoing the guided walkthrough,
-- re-inviting the bot, or re-confirming the channel list, since those are
-- captured here.
CREATE TABLE wizard_state (
    id boolean PRIMARY KEY DEFAULT true,
    CONSTRAINT wizard_state_singleton CHECK (id),
    step text NOT NULL DEFAULT 'intro',
    discord_guild_id bigint,
    discord_client_id text,
    discord_oauth_redirect_uri text,
    channels_confirmed boolean NOT NULL DEFAULT false,
    preflight_results jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
