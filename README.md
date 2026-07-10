# Threadbare

A read-only, phpBB-style web interface for browsing the history of a Discord server.

Discord's client is built for the present moment: reading deep history means fighting infinite scroll, lazy loading, and a search feature designed for finding one message rather than reading a conversation. Threadbare presents the same content as a classic forum instead — categories, boards, topics, numbered pages, permalinks, and real search — while staying fully compliant with Discord's Terms of Service and Developer Policy by operating exclusively through a bot account that the server's moderators approve and install.

Threadbare is a **cache**, not an archive. Deletions on Discord propagate to Threadbare, permissions are respected, and Discord remains the source of truth. See [`DESIGN.md`](./DESIGN.md) for the full design doc, including the migration path beyond v1.

## Status

v1 is built: sync worker, forum web app, four themes, the Discord OAuth login gate + mod
admin page, and a guided first-run setup wizard are all in place. Docker Compose deployment
and VPS hosting docs (below) are done too. See [`ROADMAP.md`](./ROADMAP.md) for exactly
what's shipped, what's left (self-host and cloud/CDK hosting docs, a nightly config-table
backup job), and in what order it was built.

## Why

Discord is where a lot of communities live now, but it's a bad medium for anything you'd want to go back and read — old debates, recommendation threads, a running lore doc a community built by accident. Threadbare gives that history a shape suited to reading: pages, permalinks, and search that returns a passage instead of a single line.

## How it works

Three long-running processes plus a database:

- **Sync worker** — backfills channel history, holds a live Discord gateway connection to apply new messages/edits/deletions as they happen, and runs a nightly reconciliation sweep to repair anything the gateway missed.
- **Web app** — server-side-rendered forum pages (no SPA). Fast paint, permalinkable, works the way old forums worked.
- **Postgres** — system of record for the mirror, with full-text search via `tsvector`/GIN.

An attachment proxy endpoint refreshes Discord's signed, expiring CDN URLs on demand rather than mirroring files locally.

Full architecture, data model, and compliance rationale: [`DESIGN.md`](./DESIGN.md).

## Compliance posture

Threadbare only ever talks to Discord as a bot, never as a user, and only accesses what the installing server's mods explicitly enable:

- Bot-token access only — no user tokens, ever.
- Deletions on Discord are deleted from Threadbare, both in near-real-time (gateway events) and via nightly reconciliation.
- v1 indexes only channels every member can already see (`@everyone`-readable), and requires server membership + login to read even that.
- Minimal data collection: display names, avatars, and message content needed for rendering. No emails, presence, or per-user reaction identity.
- No local backups of mirrored content — Discord is the sole source of truth, so deletion honoring is unconditional rather than "within backup retention."

## Getting started

Runnable as a product now: `docker compose up` brings up the whole stack and serves a guided
setup wizard that walks you through creating the Discord bot, running preflight checks, and
choosing which channels to index (see [`DESIGN.md` §8](./DESIGN.md#8-onboarding-and-setup) for
the full design). See **Deployment** below for the recommended path (a small VPS).

To work on Threadbare itself, see [`DEVELOPMENT.md`](./DEVELOPMENT.md) for environment setup,
running tests, and configuring a test Discord bot.

## Deployment

The stack is three always-on processes plus Postgres (see [How it works](#how-it-works)
above) — the sync worker holds a persistent Discord gateway connection, so this isn't a
serverless-friendly app; it needs a machine that stays on. `docker-compose.yml` runs the whole
thing: web, sync worker, Postgres (internal-only, never exposed to the host), and
[Caddy](https://caddyserver.com/) for automatic HTTPS via Let's Encrypt.

### Option B — VPS (recommended)

A $5–10/month instance (Hetzner, DigitalOcean, Vultr, etc.) is comfortable at 2GB RAM.

1. Provision a box running Ubuntu LTS, and [install Docker + the Compose
   plugin](https://docs.docker.com/engine/install/).
2. Clone this repo onto the box, then `cp .env.example .env` and fill in **at minimum**
   `POSTGRES_PASSWORD` (any strong random value) and `THREADBARE_DOMAIN` (the domain you're
   pointing at this box). Everything Discord-specific is collected by the setup wizard once
   the stack is up — don't fill those in by hand.
3. Point a DNS `A` record for that domain at the box's public IP.
4. `docker compose up -d`.
5. Visit `https://<your-domain>` — the setup wizard takes it from there (bot creation,
   preflight checks, the bot invite link, channel selection, then the OAuth login gate). When
   it finishes, run `docker compose restart sync-worker` once — the web app already picks up
   the new configuration in place with no restart, but the sync worker only reads config at
   its own startup.

Gotchas worth knowing before you go further:

- Set up unattended security upgrades on the box — it's the internet-facing part of this
  stack that isn't Threadbare's own code.
- Postgres has no published port in `docker-compose.yml` on purpose (internal Docker network
  only) — don't "fix" this by exposing it.
- There's no automated backup job yet (see [`ROADMAP.md`](./ROADMAP.md) §8) — mirrored message
  content is never backed up by design (Discord is the source of truth), but the small
  Threadbare-native config isn't backed up automatically either yet. An occasional VPS
  snapshot is a reasonable stopgap in the meantime.
- If the domain ever changes, update `THREADBARE_DOMAIN` in `.env` **and** the OAuth redirect
  URI registered in the Discord developer portal — they have to match exactly.

### Options A and C

Self-hosting on your own hardware (Tailscale/Cloudflare Tunnel guidance) and a cloud/CDK
deployment path are both designed (see [`DESIGN.md` §8.4](./DESIGN.md#84-hosting-options)) but
not yet documented here — tracked in [`ROADMAP.md`](./ROADMAP.md) §8. Option B's Docker Compose
stack is the same stack either path would use; only the docs and (for Option C) an IaC template
are missing.

## License

TBD.
