# Development guide

Everything needed to get from a clone of this repo to a passing test suite. This covers dev-environment setup only — for the design, see [`DESIGN.md`](./DESIGN.md); for what's being built and in what order, see [`ROADMAP.md`](./ROADMAP.md); for repo-specific working conventions (stack choices, TDD, commit policy), see [`CLAUDE.md`](./CLAUDE.md).

## Prerequisites

Install these once per machine:

- **[uv](https://docs.astral.sh/uv/)** (`brew install uv`) — manages the Python version, virtualenv, and dependencies for this project. Chosen over Poetry/pyenv+pip because it can pin and install its own isolated Python version per project, independent of whatever Python your system happens to have — no gambling on day-one compatibility with a just-released system Python. It's also a single static binary, in keeping with the project's "minimal moving parts" ethos ([`DESIGN.md`](./DESIGN.md) §9).
- **[Docker](https://www.docker.com/) + Compose** — runs Postgres locally. Not installed natively (no `psql` needed on the host); `docker compose exec postgres psql ...` is the way in, which keeps the local Postgres version identical to what's actually deployed.
- **Playwright's browser binaries** — installed via `uv run playwright install` once dependencies are synced (below); this is a one-time download, not a system package.

Not needed yet: Node/npm tooling. v1 has no client-side React component planned — add JS tooling only when a specific feature genuinely needs it, per `CLAUDE.md`.

## Setup

```bash
# Pin the project's Python version and install dependencies (creates .venv)
uv sync

# One-time download of the browser Playwright drives for end-to-end tests
uv run playwright install chromium

# Local config — copy the template and fill in real values (see "Configuration" below)
cp .env.example .env

# Start local Postgres (dev + test databases)
docker compose -f docker-compose.dev.yml up -d

# Apply the database schema
uv run threadbare-migrate

# Confirm the harness works end-to-end
uv run pytest
```

`uv run pytest` should discover and pass the unit and integration tests. If they pass, the toolchain is working and you're ready to start building against a `ROADMAP.md` item.

## Day-to-day commands

- **Run unit + integration tests** (the default, real Postgres required): `uv run pytest`
- **Run only unit tests** (no Postgres needed): `uv run pytest tests/unit`
- **Run only integration tests**: `uv run pytest tests/integration`
- **Run end-to-end (Playwright) tests**: `uv run pytest tests/e2e` — **always run this separately**, never together with the other tiers in one `pytest` invocation. pytest-playwright's sync driver and pytest-asyncio's runner corrupt each other's event-loop state when collected into the same session (a real upstream friction point between the two plugins, not a bug in this codebase) — `tests/e2e` is deliberately excluded from the default `testpaths` in `pyproject.toml` for this reason.
- **Run live-Discord tests** (opt-in, needs `.env` secrets): `uv run pytest tests/live_discord -m live_discord` — excluded by default via `addopts` so a plain `uv run pytest` never touches the network.
- **Apply database migrations**: `uv run threadbare-migrate` (reads `DATABASE_URL`; the integration suite applies migrations to `TEST_DATABASE_URL` itself, automatically, before its tests run)
- **Run the web app**: `uv run threadbare-web` — dev server on `http://localhost:5000` (on macOS, AirPlay Receiver squats on port 5000 by default; disable it in System Settings → General → AirDrop & Handoff, or the request just silently 403s from `ControlCenter`, not Flask). Every page now sits behind the Discord OAuth login gate (§6) — any member of `DISCORD_TEST_GUILD_ID` may log in; only members with Manage Server/Administrator on that guild can reach `/admin/`. Needs `DATABASE_URL` pointing at a database with real mirrored content to show anything on the board index.
- **Lint**: `uv run ruff check .`
- **Format**: `uv run ruff format .`
- **Pre-commit hooks** (ruff check + format, run automatically on `git commit` once installed): `uv run pre-commit install` (one-time), or run ad hoc with `uv run pre-commit run --all-files`

Following `CLAUDE.md`: write the failing test first, then the implementation. Every feature needs both a unit test and, where it touches a user-facing flow, an end-to-end test — not one or the other. For this project that spans four tiers: pure unit tests, DB-backed integration tests (`tests/integration`, real Postgres, isolated per-test via transaction rollback), live-Discord smoke tests (`tests/live_discord`, opt-in), and browser-driven Playwright tests (`tests/e2e`) — now exercising the real web app.

Note: `tests/integration/web/` and `tests/e2e/` use plain sync test functions rather than this project's usual `async def`, and hand-roll their own `asyncio.run()`-based fixtures instead of the shared `db_conn` fixture — Flask's `flask[async]`/asgiref bridge and pytest-asyncio's session-wide event loop don't tolerate each other. See `ROADMAP.md` §4 and `DESIGN.md` §10 for the full reasoning; follow the existing pattern in those directories rather than reintroducing `async def test_...` there.

## Test Discord server & bot

Set up a dedicated test server before working on the sync worker. This is worth doing, not skipping, for a few reasons:

- The sync worker's core logic — backfill, gateway events, permission computation — can't be meaningfully exercised without a real guild connection. There's no mock for Discord's gateway worth building.
- It keeps development entirely separate from any real community's server: no risk of early, untested code touching real members' data or real deletions.
- The onboarding wizard (`DESIGN.md` §8) and its preflight checks (Message Content intent, permission overwrites, OAuth redirect) are themselves features that need a real bot and a real guild to test against.
- A disposable server can be seeded with a large volume of synthetic messages to exercise pagination, backfill, and search at realistic scale without waiting on organic traffic — this is how you'll eventually validate the "million-message channel" acceptance criterion in `ROADMAP.md`.

Setup (one-time, manual — this lives outside the repo):

1. Create a new personal Discord server to act as your dev/test guild. Free, instant, disposable.
2. In the [Discord Developer Portal](https://discord.com/developers/applications), create an Application and a Bot under it.
3. Enable the **Message Content** intent on the Bot tab. This is the single most common Discord bot gotcha (`DESIGN.md` §8.2) — miss it and every message body silently arrives empty.
4. Also enable the **Server Members Intent** on the Bot tab — needed for `GUILD_MEMBER_UPDATE` to fire at all, which keeps a renamed member's `display_name` fresh even if they never post again (see `events.handle_member_update`).
5. Invite the bot to your test server using the `bot` scope with only `View Channels` + `Read Message History` permissions — the same minimal set the setup wizard will eventually request for real installs.
6. Under OAuth2, add a redirect URI for local dev (e.g. `http://localhost:5000/oauth/callback`).
8. Copy the bot token, OAuth client ID/secret, and your test server's guild ID into your local `.env` — never into `.env.example`, and never commit them.
9. Create a webhook on the test server's `#general` (Server Settings → Integrations → Webhooks → New Webhook), and copy its URL into `.env` as `DISCORD_TEST_WEBHOOK_URL`. This is the test-only posting actor for `tests/live_discord/test_full_lifecycle.py`, which posts, edits, and deletes messages to exercise the sync worker's live gateway handlers (`on_message`, `on_raw_message_edit`, `on_raw_message_delete`) end to end.

   A webhook rather than a second bot application: `discord.Webhook.from_url(...)` gives full CRUD over its own messages (`.send()` / `.edit_message()` / `.delete_message()`) via the same discord.py client, with no second application to register, no extra token to manage, and — critically — no need to grant the sync-worker bot itself any write permissions. The bot's permissions stay exactly `View Channels` + `Read Message History`, matching what the real onboarding wizard requests; the webhook is a separate identity that only this test suite uses.
10. Create one persistent thread on `#general` (post a message, then "Create Thread" on it, or right-click the channel) and copy its ID into `.env` as `DISCORD_TEST_THREAD_ID`. Needed by `tests/live_discord/test_thread_backfill.py`: Discord webhooks can only auto-create threads in *forum* channels (`400 Bad Request: Webhooks can only create threads in forum channels` — found the hard way), so a plain text-channel thread can't be spun up on the fly per test run the way the webhook itself can post/edit/delete top-level messages. Getting its ID requires Developer Mode (User Settings → Advanced → Developer Mode) so "Copy Thread ID" appears in the right-click menu. Reused (never deleted) across test runs — the tests post/edit/delete their own messages inside it, not the thread itself.
11. Create one persistent forum channel (Server Settings → Channels → Create Channel → Forum) and copy its ID into `.env` as `DISCORD_TEST_FORUM_CHANNEL_ID`. Needed by `tests/live_discord/test_forum_channel.py`. Unlike the plain-text thread above, a webhook posting into this channel *can* auto-create a new forum post per test run via `thread_name=` — only the parent forum channel itself needs to be pre-created and persistent.
12. Create a second webhook, this one bound to the forum channel (Server Settings → Integrations → Webhooks → New Webhook, select the forum channel), and copy its URL into `.env` as `DISCORD_TEST_FORUM_WEBHOOK_URL`. Webhooks can't post cross-channel, so the existing `#general` webhook can't be reused here.
13. Optional, once the sync worker exists: a small script to post a batch of synthetic messages into the test server, for exercising pagination/backfill/search at volume.

## Configuration

- **`.env`** (gitignored, never committed) — your real local secrets: bot token, OAuth credentials, database URL, session secret.
- **`.env.example`** (committed) — the template documenting every key the app needs, with placeholder values and comments. Keep this in sync whenever a new config value is introduced.
- **`docker-compose.dev.yml`** — local Postgres only (not the full deployment stack — that's a `ROADMAP.md` §8 item, once the web app and sync worker exist). Creates two databases, `threadbare_dev` (`DATABASE_URL`) and `threadbare_test` (`TEST_DATABASE_URL`), so the test suite never touches dev data.
- **Production secrets** are a separate, later concern — each hosting option in `DESIGN.md` §8.4 already has its own secrets story (Compose `.env` for self-host/VPS, CDK-managed secrets for the cloud option). Nothing to set up for that yet.
