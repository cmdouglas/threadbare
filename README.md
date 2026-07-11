# Threadbare

A read-only, phpBB-style web interface for browsing the history of a Discord server.

Discord's client is built for the present moment: reading deep history means fighting infinite scroll, lazy loading, and a search feature designed for finding one message rather than reading a conversation. Threadbare presents the same content as a classic forum instead — categories, boards, topics, numbered pages, permalinks, and real search — while staying fully compliant with Discord's Terms of Service and Developer Policy by operating exclusively through a bot account that the server's moderators approve and install.

Threadbare is a **cache**, not an archive. Deletions on Discord propagate to Threadbare, permissions are respected, and Discord remains the source of truth. See [`DESIGN.md`](./DESIGN.md) for the full design doc, including the migration path beyond v1.

## Status

v1 is built: sync worker, forum web app, four themes, the Discord OAuth login gate + mod
admin page, and a guided first-run setup wizard are all in place. Docker Compose deployment,
self-host/VPS/cloud-CDK hosting docs (below), and a production-grade (gunicorn) web server are
done too. See [`ROADMAP.md`](./ROADMAP.md) for exactly what's shipped, what's left (a nightly
config-table backup job), and in what order it was built.

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
   it finishes, the `web` container restarts itself automatically within a few seconds (the
   wizard hands off by exiting the process; Compose's `restart: unless-stopped` policy brings
   it back up already configured, now served by gunicorn instead of the wizard's dev server) —
   the finish page redirects itself once that's done. You still need to run
   `docker compose restart sync-worker` once by hand — it only reads configuration at its own
   startup and has no way to notice the change on its own.

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

### Option A — Self-host on your own hardware

The cheapest option: run `docker compose up` on any machine that stays on — a desktop, a home
server, or a Raspberry Pi–class box (the whole stack idles under 1GB RAM). Same
`docker-compose.yml` as Option B; the only real difference is *reachability*.

1. Install Docker + the Compose plugin on the machine, clone the repo, `cp .env.example .env`
   and fill in `POSTGRES_PASSWORD` (`THREADBARE_DOMAIN` only matters once you've picked a
   reachability option below).
2. Pick how the outside world reaches the box:
   - **Tailscale** — install the client on the box and on every device that should have
     access. Zero config beyond that, and it's the simplest option for a handful of trusted
     users (e.g. just the mod team). No public exposure at all.
   - **Cloudflare Tunnel** — gives you a real public hostname with TLS and no port forwarding,
     the better choice if the whole community should be able to reach it from anywhere. Point
     `THREADBARE_DOMAIN` at the hostname Cloudflare gives you and run `cloudflared` alongside
     the compose stack.
   - Classic port-forwarding + dynamic DNS works too, but isn't recommended: residential IPs
     churn, and every time yours changes you have to update both `THREADBARE_DOMAIN` and the
     OAuth redirect URI registered in the Discord developer portal, or logins start failing
     with an opaque error.
3. `docker compose up -d`, then visit whichever hostname you set up — the setup wizard takes
   it from there exactly as in Option B.

A couple of things worth knowing:

- If you're only ever accessing this from the same machine, `localhost` as the domain works
  fine and needs none of the above — just skip straight to `docker compose up -d`.
- The sync worker needs no inbound ports at all, ever — only the web app does, so Tailscale/
  Cloudflare Tunnel only need to reach the `web` (really, `caddy`) container.
- Some residential ISP terms technically prohibit "running a server." In practice this never
  comes up at hobby scale, but it's worth knowing before you tell people about it.

### Option C — Cloud via infrastructure-as-code

A TypeScript CDK app lives in [`deploy/cdk/`](./deploy/cdk/README.md): Fargate for the web app
and sync worker, an ALB + ACM certificate for the web app only, and Postgres as a Fargate
service with an EBS-backed volume (RDS documented as a commented-out alternative). See that
directory's own README for setup, secrets, and — importantly — why the first-run setup wizard
doesn't apply to this path (Fargate tasks share no filesystem for it to write `.env` to).

**Verified**: `cdk synth` produces valid CloudFormation for all five stacks. **Not verified**:
a real `cdk deploy` — no AWS account is available in this environment, so ALB reachability,
ACM validation, and the EBS volume actually attaching are unexercised. Flagged here rather than
assumed working; see `DESIGN.md` §10.

## Upgrading

The full contract any future release must honor (additive-only migrations, config
backward-compatibility, the app refusing to boot on a stale schema rather than misbehaving) is
in [`DESIGN.md` §7](./DESIGN.md#upgrade-contract). In practice:

- **Options A/B (Docker Compose)**: `./scripts/upgrade.sh` — fetches, fast-forwards, rebuilds,
  and restarts the stack. Migrations apply automatically (the existing `depends_on` gate
  already runs `migrate` before `web`/`sync-worker` start on every `docker compose up`).
- **Option C (CDK)**: `./deploy/cdk/upgrade.sh -c ...` (same context flags as `cdk deploy`) —
  deploys every stack, then automatically runs the migrate task via the run-command CDK
  already prints, instead of you having to copy-paste it by hand.

Either way, check the admin page's **Version** section (`/admin/`) afterward to confirm the
running version and latest applied migration match what you expect.

## License

TBD.
