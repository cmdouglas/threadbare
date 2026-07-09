# Roadmap: v1

v1 scope, in the order it makes sense to build it. Full rationale for each item lives in [`DESIGN.md`](./DESIGN.md); this file tracks build order and progress. Estimate for the whole milestone: **roughly one focused week, plus three days** (§6).

Everything here targets a single Discord server, public (`@everyone`-readable) channels only, membership-gated access. Role-gated channels, permission mirroring, and everything else in the migration path live in `DESIGN.md` §7 and are explicitly out of scope for v1.

## 1. Sync worker (~2–3 days)

- [x] Discord bot connection (discord.py or discord.js), gateway + REST
- [ ] Checkpointed initial backfill of in-scope channels and threads (resumable across restarts)
- [ ] Live event handling: `MESSAGE_CREATE`, `MESSAGE_UPDATE`, `MESSAGE_DELETE`, `MESSAGE_DELETE_BULK`, thread lifecycle, channel updates
- [ ] Public-channel computation: `channels.is_public` derived from role/channel overwrites, recomputed on `CHANNEL_UPDATE` and role events; content removed from index if a channel stops being public
  - [x] Core logic: `compute_is_public()` (full overwrite-precedence matrix) + `refresh_channel_public_status()` (purges content on public → gated), unit + integration tested
  - [ ] Wired to live `CHANNEL_UPDATE`/role events (Phase D)
- [ ] Nightly reconciliation sweep re-walking recent history to repair missed events
- [ ] Rate-limit-aware backfill (honors headers, backs off)
- [ ] `sync_state` checkpoints + heartbeat row for monitoring

## 2. Data model (Postgres)

- [x] Core tables: `guilds`, `channels`, `threads`, `users`, `messages`, `attachments`, `reactions`, `sync_state` (§4.1)
- [x] `tsvector`/GIN full-text index on `messages`
- [ ] Hard-delete semantics for messages/attachments/reactions (no soft-delete flags)

## 3. Rendering (~1–2 days)

- [ ] Discord-flavored markdown rendering (lean on an existing library, accept ~80% fidelity initially)
- [ ] Custom emoji, mentions resolved to display names
- [ ] Reply-chain quoting as classic forum quote blocks
- [ ] Embeds, spoilers, aggregate reaction counts

## 4. Forum web app (~2–3 days)

- [ ] Board index: categories/boards with post counts, last-post author/time
- [ ] Paginated topic/board reading (25 posts/page default), first/prev/next/last, jump-to-date
- [ ] Permalinks per message + "view on Discord" deep link
- [ ] Full-text search with author/channel/date-range filters, results link into context
- [ ] User pages: display name, avatar, post count, recent posts (indexed content only)
- [ ] Freeform-channel handling: both weekly pseudo-topics and continuous paginated view (open question in §10 — ship both, no default judgment yet)
- [ ] CSS-custom-property theme contract (markup themed from the start, not retrofitted)
- [ ] Attachment proxy endpoint (`/att/{attachment_id}`) with signed-URL refresh + expiry cache

## 5. Themes (~1 day)

- [ ] subSilver-ish (default)
- [ ] vBulletin dark
- [ ] Terminal (green-on-black monospace)
- [ ] Plain (reference implementation for future theme authors)
- [ ] `prefers-contrast` and `prefers-reduced-motion` support across all four

## 6. Access control (~1 day)

- [ ] Discord OAuth (`identify` + `guilds` scopes) login gate — any guild member may read
- [ ] Mod admin page: per-channel indexing toggle, trigger re-backfill, sync health view

## 7. Setup wizard (~1 day)

- [ ] First-run detection (unconfigured install serves the wizard, not the forum)
- [ ] Guided bot-creation walkthrough with inline screenshots
- [ ] Bot invite URL generation (`bot` scope, minimal permissions: `View Channels` + `Read Message History`)
- [ ] Preflight checks (§8.2): Message Content intent, per-channel permission verification, OAuth redirect URI round-trip, token shape/identity validation
- [ ] Channel list with indexing toggles, computed public/gated status, estimated message counts
- [ ] Resumable wizard state (safe to abandon and re-run)
- [ ] Generated mod-facing pitch kit (what's stored, how deletions propagate, admin page link)

## 8. Deployment (~1–1.5 days, CDK severable as its own 0.5 day)

- [ ] Docker Compose stack: web, sync worker, Postgres, Caddy (TLS via Let's Encrypt)
- [ ] Option A docs: self-host (Tailscale / Cloudflare Tunnel guidance)
- [ ] Option B docs: VPS (recommended default) — provision → Docker → DNS → done
- [ ] Option C: `deploy/cdk/` TypeScript CDK app (Fargate ×2, ALB+ACM for web only, Postgres sidecar w/ EBS, RDS as commented-out alt)
- [ ] Nightly dump of Threadbare-native tables only (mod config, setup state) — no message backups

## v1 acceptance criteria

Pulled from `DESIGN.md` §6 — the milestone isn't done until these hold:

- [ ] A million-message channel backfills unattended (resumable across restarts), then browses at server-side page-load times under 200ms
- [ ] Killing the sync worker for an hour and restarting produces a fully consistent mirror after the next reconciliation pass
- [ ] Deleting a message on Discord removes it from Threadbare within seconds via gateway; reconciliation catches gateway-outage deletions within 24h
- [ ] A channel switched from public to role-gated disappears from the index automatically

## After v1

Not this milestone — tracked in `DESIGN.md` §7 for when/if they happen: role-gated channels with permission mirroring (Phase 2), reading-experience depth like unread tracking (Phase 3), public/logged-out access (Phase 4), multi-guild hosting (Phase 5), other chat platforms (Phase 6).
