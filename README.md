# Threadbare

A read-only, phpBB-style web interface for browsing the history of a Discord server.

Discord's client is built for the present moment: reading deep history means fighting infinite scroll, lazy loading, and a search feature designed for finding one message rather than reading a conversation. Threadbare presents the same content as a classic forum instead — categories, boards, topics, numbered pages, permalinks, and real search — while staying fully compliant with Discord's Terms of Service and Developer Policy by operating exclusively through a bot account that the server's moderators approve and install.

Threadbare is a **cache**, not an archive. Deletions on Discord propagate to Threadbare, permissions are respected, and Discord remains the source of truth. See [`DESIGN.md`](./DESIGN.md) for the full design doc, including the migration path beyond v1.

## Status

v1 is built: sync worker, forum web app, four themes, the Discord OAuth login gate + mod
admin page, and a guided first-run setup wizard are all in place. Docker Compose deployment,
self-host/VPS/cloud-CDK hosting docs (see [Deployment](#deployment) below and
[`docs/self-hosting.md`](./docs/self-hosting.md)), and a production-grade (gunicorn) web server
are done too. See [`ROADMAP.md`](./ROADMAP.md) for exactly what's shipped, what's left (a nightly
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

A $5–10/month instance (Hetzner, DigitalOcean, Vultr, etc.) is comfortable at 2GB RAM: provision
an Ubuntu box, install Docker, point a DNS record at it, `docker compose up -d`, then let the
setup wizard take it from there. This is the easiest path if you don't already have a machine
that's always on.

### Option A — Self-host on your own hardware

The cheapest option: run the same `docker-compose.yml` on any machine that stays on — a desktop,
a home server, or a Raspberry Pi–class box (the whole stack idles under 1GB RAM). The only real
difference from Option B is *reachability* — how the outside world (or just your mod team)
gets to a box that isn't sitting behind a real public IP and domain already.

**Full step-by-step instructions for both options — including DNS, firewall, SSH, and
troubleshooting, written for admins who haven't run a server before — are in
[`docs/self-hosting.md`](./docs/self-hosting.md).** Once Docker's installed and the repo's
cloned, `./scripts/install.sh` automates the rest of either option: prompts for your site's URL,
writes `.env`, and starts the stack.

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
