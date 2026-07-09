# Threadbare

A read-only, phpBB-style web interface for browsing the history of a Discord server.

Discord's client is built for the present moment: reading deep history means fighting infinite scroll, lazy loading, and a search feature designed for finding one message rather than reading a conversation. Threadbare presents the same content as a classic forum instead — categories, boards, topics, numbered pages, permalinks, and real search — while staying fully compliant with Discord's Terms of Service and Developer Policy by operating exclusively through a bot account that the server's moderators approve and install.

Threadbare is a **cache**, not an archive. Deletions on Discord propagate to Threadbare, permissions are respected, and Discord remains the source of truth. See [`DESIGN.md`](./DESIGN.md) for the full design doc, including the migration path beyond v1.

## Status

Pre-implementation. The design is written; see [`ROADMAP.md`](./ROADMAP.md) for what's being built for v1 and in what order.

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

Not yet runnable — installation will be a guided setup wizard (`docker compose up` → wizard walks you through creating the Discord bot, running preflight checks, and choosing which channels to index). See [`DESIGN.md` §8](./DESIGN.md#8-onboarding-and-setup) for the full onboarding design.

## License

TBD.
