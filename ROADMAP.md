# Roadmap: v1

v1 scope, in the order it makes sense to build it. Full rationale for each item lives in [`DESIGN.md`](./DESIGN.md); this file tracks build order and progress. Estimate for the whole milestone: **roughly one focused week, plus three days** (§6).

Everything here targets a single Discord server, public (`@everyone`-readable) channels only, membership-gated access. Role-gated channels, permission mirroring, and everything else in the migration path live in `DESIGN.md` §7 and are explicitly out of scope for v1.

## 1. Sync worker (~2–3 days)

- [x] Discord bot connection (discord.py or discord.js), gateway + REST
- [ ] Checkpointed initial backfill of in-scope channels and threads (resumable across restarts)
  - [x] Channel message backfill: paginated, checkpointed, idempotent (dedup on rerun), resumable across restarts — unit, integration, and live tested
  - [x] Thread backfill (threads have no permission overwrites of their own — visibility keys off the parent channel)
    - Along the way, fixed a live bug found by inspection (not yet hit in practice): the schema/event-handler plumbing for thread messages already existed, but no code anywhere created a `threads` row — a real thread message would have `ForeignKeyViolation`d on `messages.thread_id` the moment it arrived via gateway. Fixed with `upsert_thread()` + self-healing wiring in `events.handle_message_create/edit`, same pattern as `discover_channels()`.
    - `discover_active_threads()` (one non-paginated `Guild.active_threads()` call, run every `on_ready`) covers active threads; `backfill_guild_threads()` covers archived threads too (`TextChannel.archived_threads(private=False)`, folded into the same walk as backfilling them) — both gated on the parent channel being public+indexed and non-forum. New `thread_sync_state` table (migration `0003`) checkpoints thread backfill separately from channel `sync_state`, following the `channels`/`threads` precedent of separate tables per concept rather than a shared nullable-pair schema. `backfill_thread()` is a structural twin of `backfill_channel()`, matching the precedent `reconcile_channel()` already set, and shares channel backfill's concurrency semaphore rather than a separate budget.
    - Known, permanent gap (not a bug): private archived threads the bot hasn't joined are invisible without `Manage Threads`, which the sync worker deliberately doesn't request (minimal-permissions design). Documented in `DESIGN.md` §10.
    - Unit, integration, and live tested (`tests/live_discord/test_thread_backfill.py`) — including a live-discovered race (`wait_until_ready()` resolving before `on_ready()`'s discovery actually finishes) and a live-discovered Discord API constraint (webhooks can only auto-create threads in forum channels, so the live tests target one persistent manually-created test thread, `DISCORD_TEST_THREAD_ID`, rather than creating one per run).
  - [ ] Forum-channel branch (forum channels have no top-level history; everything lives in threads)
- [ ] Live event handling: `MESSAGE_CREATE`, `MESSAGE_UPDATE`, `MESSAGE_DELETE`, `MESSAGE_DELETE_BULK`, thread lifecycle, channel updates
  - [x] Message create/edit/delete/bulk-delete — wired into `ThreadbareClient`, unit-equivalent + integration tested; edits reuse the same upsert path as create
  - [x] Full-lifecycle live test (post → edit → delete, verified read back via the sync worker) — see the "Full-lifecycle live testing" item below
  - [ ] Thread lifecycle (create/update/delete) — deferred until reconciliation (Phase E) exists as a backstop; discord.py's thread-delete event coverage is unreliable for threads not already cached, per the sync worker plan's risk notes, so this needs the reconciliation sweep to be trustworthy rather than a live-event-only implementation
- [x] Public-channel computation: `channels.is_public` derived from role/channel overwrites, recomputed on `CHANNEL_UPDATE` and role events; content removed from index if a channel stops being public
  - Core logic (`compute_is_public()`, `refresh_channel_public_status()`) and live wiring (`on_guild_channel_update`, `on_guild_role_update`, `on_guild_role_delete`) both done, unit/integration/live tested
- [x] Nightly reconciliation sweep re-walking recent history to repair missed events
  - `reconcile_channel()` re-walks a lookback window and converges local state (upserts repair missed creates/edits, a diff against what's still on Discord repairs missed deletes) — unit, integration (including the exact "kill worker for an hour, restart" drift scenario against real Postgres), and live tested
  - `reconciliation_loop()` runs it immediately on startup (catch-up) then nightly; only touches channels already `is_public`+`indexed`, so it can't accidentally re-add content to a channel that's supposed to be gated
  - Thread reconciliation deferred alongside thread lifecycle (§1 above) — thread backfill itself is now done, but reconciling threads (repairing missed thread edits/deletes) is a separate scope boundary, not yet built
- [x] Rate-limit-aware backfill (honors headers, backs off)
  - discord.py's `HTTPClient` already honors rate-limit headers and backs off on ordinary 429s — not rebuilt. What's actually hand-built: `BoundedHistoryFetcher` (caps concurrent in-flight fetch calls) and `RetryingHistoryFetcher` (retries specifically on `discord.RateLimited`, which discord.py only raises when its own wait would exceed `max_ratelimit_timeout`; a log line fires when a wait exceeds 1s). Both are composable wrappers around any `HistoryFetcher`, unit tested, and wired into `reconcile_guild`'s real fetcher.
- [x] `sync_state` checkpoints + heartbeat row for monitoring
  - Backfill/reconciliation checkpoints already lived in `sync_state` (per-channel, since Phase C/E). Added a separate singleton `worker_heartbeat` table (DESIGN.md §9's heartbeat is worker-global, not per-channel) updated every minute plus `last_gateway_event_at` (tracked via `on_socket_event_type`, fired on every gateway dispatch) — live-verified updating in `threadbare_dev`. The staleness *comparison*/alerting logic is explicitly left to the future admin page (`DESIGN.md` §9 frames it as something "the web app surfaces"); this only records the raw timestamps.
- [x] Channel discovery + guild-wide initial backfill orchestrator — closes the gap above
  - `discover_channels()` upserts the guild row and every channel's row (including categories, which need a row for their children's `parent_id` FK even though they have no content of their own — found via a live-test FK violation, fixed by inserting categories before their children since `fetch_channels()` doesn't guarantee order), computing `is_public` via the same `refresh_channel_public_status()` live events use. Runs on every `on_ready` (cheap, self-healing across reconnects).
  - `backfill_guild()` runs `backfill_channel()` across every in-scope channel *concurrently*, bounded by a channel-level `asyncio.Semaphore` (separate from `BoundedHistoryFetcher`'s Discord-call-level cap — a `RepositoryBackfillSink` holds its pool connection for a channel's entire backfill, so this bounds concurrent DB connections, a different resource). Pool `max_size` bumped from psycopg_pool's default-4 to 10 to give headroom.
  - Live-verified end to end from a completely empty database, with no hand-seeded channel row: real channels discovered, real messages backfilled.
- [x] Fixed `backfill_channel()`'s mid-flight commit gap
  - `BackfillSink`/`ReconciliationSink` Protocols gained `commit()`; `RepositoryBackfillSink.commit()` calls `self._conn.commit()`, `RepositoryReconciliationSink.commit()` delegates to its inner writer. `backfill_channel()` commits after each batch's `set_checkpoint()`; `reconcile_channel()` commits after each page's writes and again after the final delete+`mark_reconciled` step. A crash now loses at most the in-flight batch/page, not everything since the channel/sweep started.
  - Unit-test `FakeSink`s track `commit_count`; new tests assert commits happen per-batch/per-page (not once at the end), guarding against regressing to a trailing-commit-only shape.
  - `tests/integration/sync_worker/test_backfill.py` and `test_reconciliation.py` migrated off the shared rollback-based `db_conn` fixture onto `test_backfill_guild.py`'s pattern (own `create_pool(test_database_url)`, explicit per-table cleanup + commit) — required now that the sinks actually commit. Unit and integration suites (104 tests) pass; live kill/restart verification still pending.
- [x] Full-lifecycle live testing
  - `DISCORD_TEST_WEBHOOK_URL` added to `.env`/`.env.example`; webhook-creation step documented in `DEVELOPMENT.md`'s test-bot-setup section, including why a webhook rather than elevating the sync-worker bot's own permissions. Manually verified the webhook itself first (GET for metadata, then a real POST/PATCH/DELETE cycle) before writing the test.
  - `tests/live_discord/test_full_lifecycle.py` starts a real `ThreadbareClient`, posts/edits/deletes via `discord.Webhook.from_url(webhook_url, client=client)`, and polls Postgres for each change (create/edit/delete) rather than sleeping a fixed amount.
  - One real race found and fixed along the way: `client.wait_until_ready()` resolves as soon as discord.py's own internal READY handling completes, which is *before* `ThreadbareClient.on_ready()`'s `discover_channels()` call has necessarily finished — posting immediately after `wait_until_ready()` could hit a `ForeignKeyViolation` on `messages.channel_id` because the channel row didn't exist yet. Fixed by polling until `channels` has rows before proceeding, not just waiting on readiness.
  - This exercises the live gateway path (`on_message`, `on_raw_message_edit`, `on_raw_message_delete`) end to end for the first time — everything before this verified those handlers via fake payloads against real Postgres, or via reads only (backfill/reconciliation live tests), never a real Discord-originated write.

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
