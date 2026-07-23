# Design Document: Discord Forum Mirror

**Working title:** Threadbare
**Status:** Draft
**Author:** Charlie
**Last updated:** July 2026

---

## 1. Summary

Threadbare is a read-only, phpBB-style web interface for browsing the history of a Discord server. It exists because Discord's client is optimized for the present moment: reading deep history means fighting infinite scroll, lazy loading, and a search feature designed for finding single messages rather than reading conversations. Threadbare presents the same content as a classic forum — categories, boards, topics, numbered pages, permalinks, and real search — while remaining fully compliant with Discord's Terms of Service and Developer Policy by operating exclusively through a bot account approved and installed by the server's moderators.

Threadbare is not an archival tool. Its data store is a cache of Discord, not a replacement for it: deletions propagate, permissions are respected, and Discord remains the source of truth.

## 2. Goals and non-goals

**Goals**

- Comfortable long-form reading of server history: paginated, linkable, navigable by date.
- Full-text search across indexed channels.
- Live currency: new messages appear within seconds; edits and deletions propagate promptly.
- Full ToS/Developer Policy compliance: bot-token access only, mod-approved installation, deletion honoring, no permission bypass.
- Low operational burden: one small VPS, one Postgres instance, minimal moving parts.

**Non-goals**

- Posting, reacting, or any write path back to Discord. Threadbare is read-only by design.
- Long-term archival or export. Content deleted from Discord is deleted from Threadbare.
- Multi-server SaaS operation (see §9 for what would change). Threadbare targets a single server per deployment.
- Voice, video, stage channels, or activity content.

## 3. Constraints and compliance requirements

These are treated as hard requirements, not preferences. They shape the architecture.

1. **Bot-token access only.** All Discord API access uses a bot account invited by a user with Manage Server permission. User tokens are never used, requested, or supported, in any code path.
2. **Deletion honoring.** Discord's developer policy requires not retaining data users have deleted. `MESSAGE_DELETE` and bulk-delete events must remove content from the store, and a reconciliation sweep must catch deletions missed during gateway outages.
3. **No permission bypass.** Threadbare must never make content visible to someone who could not see it on Discord itself. In v1 this is satisfied structurally (only @everyone-readable channels are indexed); in later versions it requires active permission mirroring (§7).
4. **Minimal data collection.** Store display names, avatars, and message content necessary for rendering. Do not store emails, connection data, presence, or anything beyond what the forum view needs.
5. **Attachment handling.** Discord CDN URLs are signed and expire (~24h). Attachments are proxied on demand, not mirrored to local storage, keeping Threadbare out of the content-retention business.

## 4. Architecture

Three long-running processes plus a database:

```
┌─────────────┐   gateway (WS)   ┌──────────────┐
│   Discord    │◄────────────────►│ Sync worker  │
│     API      │                  │ (backfill +  │
│              │◄─── REST ────────│  live events)│
└──────┬──────┘                  └──────┬───────┘
       │ REST (attachment refresh)      │ writes
       │                                ▼
┌──────┴──────┐                  ┌──────────────┐
│  Web app     │◄──── reads ─────│   Postgres    │
│ (SSR forum)  │                  │  (+ tsvector) │
└─────────────┘                  └──────────────┘
```

- **Sync worker.** A single process (discord.py or discord.js) that (a) performs the initial checkpointed backfill of all in-scope channels and threads, (b) holds the gateway connection and applies `MESSAGE_CREATE` / `MESSAGE_UPDATE` / `MESSAGE_DELETE` / `MESSAGE_DELETE_BULK` / thread lifecycle / channel-update events, and (c) runs a nightly reconciliation sweep re-walking recent history to repair missed events.
- **Web app.** Server-side-rendered pages (Flask/Jinja or Express/EJS — SSR is a feature here, both for the retro aesthetic and for trivially fast paint on old-forum-density pages). No SPA. A small session layer for OAuth (v1 uses it only for membership gating).
- **Postgres.** System of record for the mirror. Full-text search via `tsvector`/GIN. Message IDs (snowflakes) provide natural ordering and free timestamp derivation.
- **Attachment proxy.** A web-app endpoint `/att/{attachment_id}` that refreshes the signed CDN URL via the API when the stored one has expired, then 302-redirects. Nothing is stored locally beyond a URL + expiry cache.

### 4.1 Data model (core tables)

| Table | Key contents |
|---|---|
| `guilds` | id, name, icon |
| `channels` | id, guild_id, parent_id (category), type, name, position, topic, `is_public` (computed), `indexed` (mod-controlled) |
| `threads` | id, parent_channel_id, name, archived, created_at, message_count |
| `users` | id, display_name, avatar_hash — refreshed lazily, no PII beyond this |
| `messages` | id (snowflake, PK), channel_or_thread_id, author_id, content, reply_to_id, edited_at, `tsv` (generated tsvector column), flags |
| `attachments` | id, message_id, filename, content_type, size, cached_url, url_expires_at |
| `reactions` | message_id, emoji, count (aggregate only — no per-user reaction identity) |
| `sync_state` | per-channel backfill checkpoints, last gateway sequence, reconciliation timestamps |

Deletion is implemented as hard row deletion (message + attachments + reactions), not soft-delete flags, to keep the compliance story unambiguous.

### 4.2 Forum mapping

| Discord concept | Forum concept |
|---|---|
| Category | Forum category |
| Text channel | Board |
| Thread / forum-channel post | Topic |
| Messages in a freeform text channel | Date-bucketed pseudo-topics (default: weekly buckets, configurable), or one continuous paginated board view — both offered; reading preference decides |
| Message | Post (numbered within its topic/board) |
| Pinned messages | Stickied topic per board |

Because messages live in Postgres, real offset pagination ("page 47 of 212") works, alongside date-jump navigation derived from snowflake timestamps.

## 5. Core features (the product across all versions)

1. **Board index** — categories and boards with post counts, last-post author/time.
2. **Paginated topic/board reading** — fixed posts-per-page (default 25), numbered pages, first/prev/next/last, jump-to-date.
3. **Permalinks** — stable URLs per message (`/topic/{id}/page/{n}#post-{message_id}`), plus a "view on Discord" deep link per post.
4. **Full-text search** — with author, channel, and date-range filters; results link into paginated context, not isolated snippets.
5. **Faithful rendering** — Discord-flavored markdown, custom emoji, mentions resolved to display names, reply-chain quoting rendered as classic forum quote blocks, embeds, spoilers, reactions as aggregate counts.
6. **Live sync** — new posts visible within seconds; edits marked with an "edited" timestamp; deletions removed.
7. **User pages** — display name, avatar, post count, recent posts (public content only).
8. **Mod controls** — a minimal admin page for the bot installer: choose which channels are indexed, trigger re-backfill, view sync health.
9. **Theming** — user-selectable themes with a mod-set default. Implemented as pure CSS: the templates emit stable, semantic, classed markup and every color, font, border, and radius lives in CSS custom properties, so a theme is a single stylesheet and third-party themes are a drop-in file. Display preferences (theme choice, and — as of the avatars feature below — avatar visibility) persist per user via a cookie today; see the "Display preferences" note under §6's OAuth login flow for the planned migration to account-level storage. Avatar display: each post (and the user page) shows the poster's Discord avatar, resolved directly from `users.avatar_hash` against Discord's static/unsigned avatar CDN (no signed-URL refresh needed, unlike attachments) — with a toggle to hide them.

## 6. v1 scope: public channels, membership-gated

v1 deliberately avoids the hardest problem (per-channel permission mirroring) by only indexing content that every member can already see.

**In scope**

- Index only channels readable by @everyone (computed from role/channel overwrites at sync time; re-computed on `CHANNEL_UPDATE` and role events — if a channel becomes non-public, its content is removed from the index).
- Site access gated by Discord OAuth (`identify` + `guilds` scopes): any member of the guild may log in and read. No per-channel access math — membership is the only check.
  - **Display preferences (planned migration):** theme choice and avatar visibility (§5 feature 9) are cookie-backed today, not a real per-user setting — there's no `user_preferences` table, deliberately, to avoid a first departure from this project's minimal-user-data stance ahead of actually needing it. `web/views/auth.py`'s OAuth callback already fetches the logged-in member's current Discord identity on every login, which is the natural point to also upsert a future preferences row without a new fetch path — but note it would need its own key on the session's `user_id`, not a FK assuming a `users` row already exists, since a logged-in member who's never posted has no `users` row (same gap noted for display-name refresh in ROADMAP.md's UI polish backlog).
- Full backfill + live sync + nightly reconciliation for in-scope channels and their threads.
- Features 1–9 from §5, with user pages limited to indexed content.
- Four shipped themes, because this project is for fun and the fun should be visible on day one:
  - **subSilver-ish** (the default) — beveled gradients, table-dense layouts, tiny pagination links, the full 2004 phpBB experience, lovingly recreated rather than pixel-copied;
  - **vBulletin dark** — the other half of forum nostalgia, for the servers that were *that* kind of community;
  - **Terminal** — green-on-black monospace BBS mode, which also becomes the obvious house theme if Phase 6's IRC dream ever lands;
  - **Plain** — a quiet, modern, readable theme for people who claim not to be here for the nostalgia, doubling as the reference implementation theme authors copy from.
- A high-contrast/reduced-motion pass is a property of the markup and variable system rather than a fifth theme: every shipped theme must remain legible under `prefers-contrast` and honor `prefers-reduced-motion` (relevant mostly to subSilver's ambitions).
- Attachment proxying with URL refresh.
- Single-server deployment; Docker Compose for the whole stack; first-run setup wizard (§8) so installation is a guided flow rather than an environment-variable scavenger hunt.

**Explicitly out of scope for v1**

- Private/role-gated channels in any form.
- Per-channel permission mirroring.
- Anonymous/public (non-member) access — even though the content is @everyone-readable, requiring login keeps the site from becoming an unintended public index of the server and keeps the mod conversation simple. (A mod-controlled toggle for public access is a cheap v1.x addition if the community wants it.)
- Multi-guild support.

**v1 acceptance criteria**

- A million-message channel backfills unattended (resumable across restarts) and is then browsable at page-load times under 200ms server-side.
- Killing the sync worker for an hour and restarting produces a fully consistent mirror after the next reconciliation pass.
- Deleting a message on Discord removes it from Threadbare within seconds (gateway path) and reconciliation catches gateway-outage deletions within 24h.
- A channel switched from public to role-gated disappears from the index automatically.

**v1 effort estimate:** roughly one focused week, plus three days. Sync worker 2–3 days, rendering 1–2 days, forum UI 2–3 days (the CSS-variable theme contract is part of this, not extra — it's cheaper to build the markup themed than to retrofit), the four themes 1 day (subSilver is most of it; Plain falls out of development; Terminal and vBulletin dark are variable swaps plus taste), OAuth gate + admin page 1 day, setup wizard with preflight checks 1 day, deployment paths (compose file, hosting docs for Options A/B, CDK template for Option C) 1–1.5 days. The CDK template is severable — Options A/B alone are a 0.5-day deliverable if v1 needs trimming. The estimate assumes leaning on an existing Discord-markdown rendering library and accepting its 80% fidelity initially.

## 7. Migration path to full-featuredness

Each phase is independently shippable and none requires schema-breaking changes if the v1 data model above is used (notably: `channels.is_public` is already a computed permission fact, and access checks are already centralized in one authorization module, even when that module's only rule is "is a member").

### Phase 2 — Role-gated channels with permission mirroring

The defining feature of "full." Index non-public channels and show each logged-in user exactly what they can see on Discord.

- On login (and on a refresh interval, e.g. hourly, plus on `GUILD_MEMBER_UPDATE`, now that the members intent is already requested — see below), fetch the user's roles and compute effective read permission per channel using Discord's resolution order: base @everyone permissions → role allows/denies → category overwrites → channel overwrites, with admin short-circuit. (`sync_worker/bot.py`'s `on_member_update`/`events.handle_member_update` already exists and requests the privileged `GUILD_MEMBERS` intent, shipped ahead of this phase to fix a display-name-staleness bug — see `ROADMAP.md`'s UI polish backlog. This phase's permission-set refresh can reuse that same handler/intent rather than requesting it again.)
- Cache the resulting channel-visibility set per user session; invalidate on role events.
- Search must filter by the requesting user's visibility set — this is the easy-to-forget bypass vector, so it's an explicit test target.
- Risk note: this is the fiddliest code in the project and the only place where a bug is a *disclosure* bug rather than a rendering bug. It ships behind a per-channel opt-in flag so mods enroll sensitive channels deliberately, and it merits the most test coverage in the codebase (golden tests against permission fixtures exported from a real server).
- Estimate: 3–5 days including tests.

### Phase 3 — Reading-experience depth

Quality-of-life features that make it a *good* forum rather than a functional one, in rough priority order: unread tracking and "first unread post" jumping (per-user read markers — the single most-missed forum feature); reply-chain threading view as an alternative to flat pagination; user post-history search; emoji/reaction filtering ("show me everything this server 🔥'd"); RSS/Atom feeds per board; community theme contributions beyond the four shipped in v1 (a themes/ directory plus a gallery page is the whole feature, thanks to the CSS-only theme contract). Estimate: continuous, 0.5–2 days per feature; none blocks anything.

### Phase 4 — Public web presence (optional, changes the compliance posture)

Making some or all boards readable without login, Linen/Answer Overflow style.

- Requires explicit mod opt-in per channel, a robots.txt/meta strategy decision, and a visible privacy policy covering what's shown publicly and how members can request removal.
- Adds display-name anonymization as a per-user or per-server option (generated aliases), since members consented to a Discord audience, not a Google audience.
- This phase is where the developer-policy obligations get real teeth; it should not be bundled into earlier phases casually.
- Estimate: 2–3 days of code; the real cost is the policy/consent work.

### Phase 5 — Multi-guild / hosted operation (probably never, but named so it's a decision)

Serving other communities means: tenant isolation in the schema (guild_id everywhere — already true of the v1 model), per-guild admin onboarding, and crossing Discord's bot-verification threshold at 75–100 servers, which brings identity verification and a privileged-intent approval process for the Message Content intent. This is the line between "project" and "product with support obligations." Not planned; documented so that early schema choices (they already accommodate it) aren't accidental blockers.

### Phase 6 — Other chat platforms (far-future pipedream)

The forum-reading problem isn't Discord-specific: Slack communities and IRC channels have the same "great in the moment, hostile to history" property. The long-term dream is Threadbare as a platform-agnostic forum lens with pluggable ingestion adapters.

**What the abstraction looks like.** The web app, search, and forum model already operate on a normalized shape (boards → topics → posts with authors, timestamps, attachments, edits, deletions). An ingestion adapter interface would own three responsibilities per platform: backfill (paginate history into the store), live sync (subscribe to create/update/delete events), and identity (map platform users to display entities). The Discord sync worker becomes the first implementation of that interface rather than the definition of it.

**Platform realities worth knowing before dreaming too hard:**

- **Slack** maps well — `conversations.history` gives cursor pagination, the Events API gives live sync, and its thread model is arguably *more* forum-shaped than Discord's. Two gotchas: free-tier workspaces only expose ~90 days of history through the API, so backfill depth depends on the workspace's plan, and Slack app installation requires a workspace admin, so the "mod-approved bot" social model transfers unchanged.
- **IRC** breaks the core architectural assumption. There is no server-side history: the protocol has no backfill, so Threadbare would stop being a cache of the platform and become the *system of record* from the day it joins the channel. That conflicts directly with the §1 "cache, not archive" principle and changes the compliance posture (deletion honoring becomes a Threadbare policy question, not a platform event to mirror). IRC support therefore means either accepting an archive mode with its own retention/consent story, or importing existing bouncer/ZNC logs as a one-time seed. Solvable, but it's a different product philosophically, which is exactly why it lives in Phase 6.
- **Matrix**, if the pipedream ever gets prioritized, is likely the *easiest* second platform — full server-side history, real pagination, an event model with native edits/redactions — and would be the honest way to validate the adapter interface before tackling IRC's weirdness.

**What this costs today: almost nothing, deliberately.** Building the adapter abstraction now would be over-engineering for a single-platform v1. The only cheap hedges worth taking immediately: (a) don't let Discord snowflake semantics leak beyond the ingestion layer — store a platform-native message ID *plus* an explicit `(posted_at, sequence)` sort key rather than sorting on snowflake math in queries, and (b) keep all "what platform is this" knowledge inside the sync worker and the markdown renderer, never in the forum UI. Both are near-free in v1 and are the difference between Phase 6 being a refactor versus a rewrite.

### Upgrade contract

The phases above are only "independently shippable... with no schema-breaking changes" (opening line of this section) if every release that ships one actually honors that promise. These are the hard rules, not aspirations, and the ones with real enforcement behind them are noted as such:

1. **Migrations are additive-only and forward-only.** No release ships a migration that renames, drops, or type-changes an existing column or table in a way old code depends on. A genuinely unavoidable breaking change ships as an *expand/contract* pair across two releases: release N adds the new shape and dual-writes/backfills into it; release N+1, once operators have had a chance to upgrade past N, drops the old shape. Never a single-release breaking migration.
2. **This is what makes rollback safe without down-migrations.** Because migrations only ever add, redeploying the previous code version against an already-migrated (newer) schema is always safe — old code simply ignores columns/tables it doesn't know about. Rollback is "redeploy the old image," not a down-migration mechanism; none is planned, because none is needed as long as rule 1 holds.
3. **New required config ships with either a safe default, or a guided upgrade step.** A release that needs a new Discord permission or OAuth scope reuses the setup wizard's existing preflight-check/resumable-state machinery (`wizard/preflight.py`'s per-channel ✔/✘ table, the `wizard_state` table) to detect the gap and tell the operator exactly what to fix — not a second, parallel "upgrade wizard," and never an opaque failure.
4. **Any new feature that changes what's shown/exposed defaults OFF on upgrade.** This generalizes Phase 2's already-decided "per-channel opt-in flag" (above) into a hard rule for every future phase, not just that one. An upgrade must never silently change what's visible to members.
5. **The app refuses to boot if the DB schema is behind what the running code expects.** `db/migrate.py`'s `check_schema_up_to_date()` — a read-only sibling of the migration runner, reusing the same discovery/applied-check machinery — is called at the start of both `web/cli.py::main()` and `sync_worker/cli.py`'s boot sequence. A pending migration raises `MigrationError`, caught at the top level and turned into a clear stderr message plus a non-zero exit, never a partially-working process serving broken pages or silently misbehaving. Compose's `migrate` service already runs before `web`/`sync-worker` via `depends_on`, so this is a no-op there in the normal case; it's the real safety net for Option C operators who forget `aws ecs run-task`, and for bare local dev without having run `threadbare-migrate` first.
6. **One documented, scripted upgrade procedure per deployment path.** `scripts/upgrade.sh` (Options A/B: fetch, fast-forward, rebuild, `docker compose up -d` — migrations apply automatically via the existing `depends_on` gate) and `deploy/cdk/upgrade.sh` (Option C: `cdk deploy --all` plus automatically fetching and running `ThreadbareMigrate`'s `RunTaskCommand` output, closing the manual-`run-task` gap `deploy/cdk/README.md` otherwise documents). Neither script is automated-tested (shell orchestration over real infrastructure); `scripts/upgrade.sh` is verified manually against a real, isolated Docker Compose stack, and `deploy/cdk/upgrade.sh` only via `bash -n` plus confirming `cdk synth` still emits the `RunTaskCommand` output it depends on — flagged here rather than assumed working, per this document's own convention (§10).
7. **The running version is visible.** `threadbare.__version__` (from installed package metadata) and the latest applied migration are shown on the mod admin page and via `--version` on all three CLI entry points — the concrete way an operator confirms an upgrade actually took effect, addressed to rule 6's scripts printing a reminder to check it.

**Release convention (recommended, not yet enforced):** bump `pyproject.toml`'s `version` and tag the commit (`git tag vX.Y.Z`) as part of cutting any release — the version-exposure mechanism above is only useful if that discipline is actually followed. No tag has been cut yet as of this writing; establishing the *mechanism* was this pass's scope, not deciding when v1 is "officially" 1.0.0.

## 8. Onboarding and setup

Onboarding is a first-class feature, not documentation. There are two audiences with different failure modes: the **operator** (whoever runs the deployment — probably you) and the **server mods** (who approve the bot and control what's indexed). The design goal is that a competent mod who has never created a Discord application gets from zero to "backfill running" in under fifteen minutes without reading external docs.

### 8.1 The happy path

1. `docker compose up` brings up the stack; the web app detects an unconfigured install and serves a first-run **setup wizard** instead of the forum.
2. The wizard walks through creating a Discord application and bot (with screenshots inline, since the developer portal reorganizes itself every couple of years), then accepts the bot token.
3. On token entry, the wizard runs **preflight checks** (§8.2) and refuses to proceed while any fail — every gotcha below is caught here rather than discovered as mysterious empty pages later.
4. The wizard generates the exact bot invite URL with the correct scope (`bot`) and permissions integer (`View Channels` + `Read Message History` only — requesting the minimum is also the best look in the mod-approval conversation).
5. Once the bot lands in the guild (the wizard polls and detects the join), it displays the channel list with per-channel indexing toggles, public/gated status, and estimated message counts, then starts the backfill with a visible progress page.
6. OAuth credentials for the login gate are configured last, with the wizard displaying the exact redirect URI to paste into the developer portal.

**v1 implementation deviations from the above, deliberate rather than discovered late (ROADMAP.md §7 has the full account):** step 2's screenshots are text + numbered steps instead — this pass couldn't produce authentic developer-portal screenshots, and stale placeholders seemed worse than clear copy. Step 5's "estimated message counts" is dropped (no cheap way to compute one without paginating full history, which the wizard exists to avoid pre-confirmation) and it does **not** start a real backfill or show a progress page — the web app and sync worker have no IPC today, so that's deferred; the wizard's last screen instead tells the operator to (re)start the sync worker themselves once `.env` is written, mirroring the same deferral the admin page's "trigger re-backfill" already made (§6).

### 8.2 Preflight checks and the gotchas they catch

Each of these is a real-world silent failure mode; the wizard tests for all of them explicitly:

| Gotcha | Symptom if missed | Preflight check |
|---|---|---|
| **Message Content intent not enabled** — a dashboard toggle in the Bot settings, off by default | The single most infamous Discord bot gotcha: everything *works*, but every message body arrives empty. A forum of blank posts. | Fetch one recent message post-join and verify `content` is non-empty (or intent flags via the application endpoint) |
| **Server Members intent not enabled** — another dashboard toggle, off by default | Everything works and messages arrive fine, but a member who renames on Discord and doesn't post again keeps a stale `display_name` forever (`GUILD_MEMBER_UPDATE` simply never fires without it) | None yet — unlike Message Content, there's no cheap after-the-fact signal short of observing a real member update, so this is documented in the wizard's intro step (`wizard_intro.html`) rather than preflight-checked |
| Bot invited with wrong/missing permissions | Backfill silently skips channels or 403s | Enumerate channels and verify effective `View Channel` + `Read Message History` per channel; show a per-channel ✔/✘ table |
| Channel-level permission overwrites denying the bot | A specific channel never syncs even though the server-level grant looks right — overwrites beat role grants | Same per-channel check; call out *overwrite-denied* channels distinctly so mods know where to look |
| OAuth redirect URI mismatch | Login fails with an opaque Discord error page after the user authorizes | Wizard displays the exact URI (scheme, host, port, path) to register, then performs a live round-trip test |
| Token pasted with `Bot ` prefix, whitespace, or an OAuth secret instead of the bot token | Auth failures that look like Discord being down | Validate token shape and identity (`GET /users/@me`) on entry |
| Backfill hammering rate limits | 429 cascades; in the current enforcement climate, sustained abuse risks the application | Sync worker honors rate-limit headers with backoff by design; wizard sets expectations ("~1M messages ≈ a few hours") so nobody restarts it out of impatience |
| Public-channel computation surprises | Mods assume a channel is private, but @everyone can technically read it, so v1 indexes it | Wizard's channel table shows *computed* visibility with an explanation, and requires explicit confirmation of the indexed set rather than defaulting everything on |
| Bot in the guild but setup abandoned midway | A half-configured install serving errors | Wizard state is persisted; re-running setup resumes at the last incomplete step |

### 8.3 The mod-facing pitch kit

Since the operator usually isn't the server owner, onboarding includes a generated one-page summary to hand to the mod team: what the bot reads (and the minimal permissions requested), what's stored, how deletions propagate, that mirrored content is never backed up (Discord remains the sole source of truth), and a link to the admin page they'll control. This is §10's mod-relations risk mitigation, productized — the approval conversation goes better when the answers arrive before the questions.

### 8.4 Hosting options

The repo ships with three supported deployment paths, each with its own gotchas called out: Options A/B in [`docs/self-hosting.md`](./docs/self-hosting.md) (a beginner-friendly walkthrough, linked from a short summary in `README.md`), Option C in `deploy/cdk/README.md`. One architectural fact drives all of them: **the sync worker holds a persistent gateway websocket, so Threadbare needs an always-on process.** Serverless platforms (Lambda, Cloud Run scale-to-zero, Vercel/Netlify) are structurally wrong for the sync worker no matter how attractive they look for the web app — this is called out prominently because it's the most common architectural false start for Discord bots.

**Option A — Self-host on your own machine (cheapest, most gotchas).**
`docker compose up` on any box that stays on: a desktop, home server, or Raspberry Pi–class machine (the whole stack idles under 1GB RAM; ARM images are provided). The gotchas are all about *reachability*, and they're front-loaded in the docs:

- Discord OAuth requires a redirect URI that the *browser* can reach and that's registered in the developer portal. `localhost` works fine for a single-operator install; anything shared needs a real hostname.
- For sharing without opening your home network: **Tailscale** (zero-config, members install the client — fine for a handful of trusted users) or **Cloudflare Tunnel** (public hostname, TLS, no port forwarding — the recommended path for "my whole server uses it from my closet"). Classic port-forward + dynamic DNS is documented but discouraged: residential IPs churn, which breaks the OAuth redirect URI until updated.
- Outbound-only note for the paranoid firewall: the sync worker needs no inbound ports at all; only the web app does.
- ISP fine print: some residential terms prohibit "servers"; practically this never matters at hobby scale, but it's noted.

**Option B — VPS (recommended default).**
A $5–10/month instance (Hetzner, DigitalOcean, Vultr, etc.), 2GB RAM comfortable. The docs provide a copy-paste path: provision Ubuntu LTS → install Docker → clone → `docker compose up -d` → point DNS at the box → Caddy (already in the compose file) handles TLS automatically via Let's Encrypt. Gotchas documented: set up unattended security upgrades; put Postgres's port on the internal Docker network only (the compose file already does this — called out so nobody "fixes" it); snapshot the VPS occasionally as a lazy substitute for the config-table backup job; and remember the OAuth redirect URI must be updated if the domain changes.

**Option C — Cloud via infrastructure-as-code (for the AWS-shaped brain).**
A `deploy/cdk/` directory with a small TypeScript CDK app (a Terraform variant is a welcome-PR item) defining: one small ECS Fargate service each for web and sync worker, an ALB with ACM certificate for the web app only, and Postgres. Cloud-specific gotchas the template encodes rather than documents:

- **The sync worker gets `desiredCount: 1` and no load balancer** — it's a singleton by design; two gateway connections with the same session cause event weirdness, and the template makes the wrong thing hard to express.
- **Database cost trap:** RDS's smallest sensible instance costs more per month than the entire Option B VPS. The template defaults to Postgres in a Fargate sidecar with an EBS-backed volume for hobby scale, with RDS as a commented-out alternative for anyone who wants managed backups and Multi-AZ. This is the single biggest "why is my bill $60?" surprise, so it's a default, not a footnote.
- NAT gateway avoidance: tasks run in public subnets with public IPs (outbound API access without the ~$32/month NAT tax), security-grouped to inbound-nothing for the worker and ALB-only for the web app.
- Rough honest costs in the README: Option C lands around $15–30/month even done frugally — the price of `cdk deploy` convenience over Option B.

All three paths converge on the same first-run setup wizard (§8.1), so the hosting choice only determines *where* the stack runs, never *how* it's configured. The wizard's OAuth redirect-URI preflight (§8.2) is hosting-aware: it detects the externally visible hostname and shows the exact URI for the deployment in front of it.

## 9. Operations

- Reference deployment: single VPS (Option B, §8.4), 2GB RAM comfortable, Docker Compose: web, sync worker, Postgres, Caddy for TLS. Self-hosted and CDK-based cloud deployments (Options A and C) share the same compose-defined stack.
- Monitoring: a heartbeat row the sync worker updates each minute; the web app surfaces staleness on the admin page and a simple external uptime ping alerts on web-app death. Gateway wedge (connected but silent) is caught by the heartbeat comparing last-event time against Discord activity.
- Backups: none for mirrored content — Discord is the source of truth, and re-import is the recovery path (restore config, re-run backfill, tolerate a few hours of rebuild for a large server). Only the small Threadbare-native tables are dumped nightly: mod indexing configuration, setup state, and (Phase 3) per-user read markers — kilobytes, retained 7 days. This also strengthens the compliance posture: with no message backups, deleted content doesn't persist anywhere, making deletion honoring unconditional rather than "within backup retention."
- Storage estimate: text-only messages average well under 1KB/row with indexes; a 5M-message server fits in single-digit GB.

## 10. Risks and open questions

| Risk | Mitigation |
|---|---|
| Permission-mirroring bug discloses gated content (Phase 2) | Opt-in per channel, centralized authorization module, fixture-based tests, default-deny on any resolution error |
| Discord API/policy changes (recent enforcement climate shows willingness to move) | Bot-token-only design is the durable side of the line; sync worker isolates all API surface in one process |
| Gateway event loss causing stale/undead content | Nightly reconciliation sweep; heartbeat monitoring |
| Attachment URL expiry breaking old pages | On-demand proxy refresh (§4); no local mirroring |
| Message Content intent friction | Non-issue below 75 servers (dashboard toggle); becomes an approval process only in Phase 5 |
| Mod relations: "what exactly does this thing store?" | Deletion honoring by design, minimal user table, admin visibility into indexed channels, no message backups — delivered as the generated pitch kit (§8.3) |
| Private archived threads invisible to the sync worker without `Manage Threads` | Documented limitation, not solved by requesting more permissions — consistent with the minimal-permissions design (§3/§8.2: `View Channels` + `Read Message History` only). Active threads and public archived threads are fully covered. |
| Reaction add/remove/clear/clear-emoji have no live-gateway test coverage | Webhooks (used for every other live test) can't react to messages at all — no such API exists. A live test would need the bot's own token plus a new `Add Reactions` permission, the first widening of the minimal-permissions design; deferred rather than granted. Covered instead by integration tests (fakes against real Postgres) for all four gateway paths plus the backfill/reconciliation reaction-sync path. |
| `GUILD_MEMBER_UPDATE` (display-name refresh, ROADMAP.md) has no live-gateway test coverage | Every other live test's posting actor is a webhook (`DEVELOPMENT.md`), which has no member identity to rename — there's no API to trigger a real member update without either the bot's own token renaming itself (`guild.me.edit(nick=...)`, plausible as a future addition since it needs no new permission grant) or a second real, renamable account, which this project's bot-token-only design (§3.1) doesn't provision. Covered instead by integration tests (fakes against real Postgres) for both the update-existing-row and insert-a-new-row cases. |
| **(found on a real production deployment) `sync-worker`'s config never actually refreshed on `docker compose restart sync-worker`, leaving every wizard-configured channel invisible** | Unlike `web`, which bind-mounts `./.env:/app/.env` read-write so the wizard's writes are visible on the host, `sync-worker`'s compose service had only `env_file: - .env` — Docker materializes that into the container's environment once, at container creation, and `restart` doesn't re-resolve it or recreate the container. `sync_worker/cli.py`'s `load_settings()` call goes through `config.py`'s `reload_env_file()`, but with no `.env` file on disk inside that container at all, the call was a silent no-op. Result: the sync worker kept running on stale blank Discord config, never connected to the gateway, and `discover_channels()` (the only code path that ever sets `channels.is_public`) never ran — so channels stayed at their schema-default `is_public = false` regardless of what the wizard's `/channels` step had correctly set for `indexed`, and the board list rendered empty even though setup had "succeeded." Invisible to the test suite for the same reason the earlier `web`-side version of this bug was (see `write_env_updates`/`reload_env_file`'s own tests): every `sync_worker` config test passes an explicit `env` dict, never exercises the `env is None` → real-file path against an actual bind-mount topology. Fixed by giving `sync-worker` the same bind mount, read-only (it never writes the file, only reads it) — verified manually against a real, isolated Docker Compose stack: with the mount in place, editing `.env` on the host and running a plain `docker compose restart sync-worker` was confirmed to actually pick up the new values, matching what the docs already (now correctly) tell operators to do. |
| ~~The web app's attachment-refresh call has an unverified contract and unconfirmed bot-token support~~ — **resolved, confirmed live** | Live-tested against the real test Discord server: posted an image to `#general`, backfilled it via `threadbare-sync-worker`, forced its local `url_expires_at` into the past, then hit `/att/{id}` against a real running `threadbare-web`. `refresh_attachment_urls` successfully called `POST /attachments/refresh-urls` with the bot token (no 401 — bot-token support confirmed) and the response shape matched the implemented assumption exactly (`{"refreshed_urls": [{"original", "refreshed"}, ...]}`); `parse_expiry_from_signed_url` correctly parsed the real returned `ex=` value, which matched the attachment's genuine Discord-side expiry. One real, unrelated bug surfaced by this same exercise — see the row below. |
| **(found during the live test above) `web/db.py`'s `PerRequestConnectionSource` silently discarded every write** | `conn.close()` on a connection with an open transaction does not commit it — the adapter had no explicit `commit()` and wasn't wrapped in psycopg's `async with conn:` idiom, so the attachment-refresh write (and any other web-app write) vanished the moment the connection closed, even though the route returned a normal 302 with no error. Invisible to the test suite: `tests/integration/web/`'s `FakePool` shares one already-open, never-closed connection across an entire test, so a route's writes were visible to later assertions via read-your-own-writes within the same uncommitted transaction — no test ever needed a real commit to "look" correct. Only surfaced by testing against a real, separate verifying connection (a live server, then a dedicated regression test using a second `psycopg.connect()` to check). Fixed by wrapping the yield in `async with conn:`, matching `AsyncConnectionPool.connection()`'s own documented commit-on-success/rollback-on-exception behavior; `tests/integration/web/test_db.py` now guards this specifically, using its own connection (not `FakePool`) to verify persistence. **Lesson for future work in this codebase: `FakePool`-based tests validate route logic and response shape, not whether writes actually persist — any new write path in `web/` needs at least one test structured like `test_db.py`'s, not just route-level assertions.** |
| `psycopg_pool.AsyncConnectionPool` (used everywhere else, `db/pool.py`) does not survive Flask's `flask[async]`/asgiref `async_to_sync` bridge | Confirmed by direct experimentation (not a guess) while building §4: the pool's background maintenance tasks get orphaned across the thread/event-loop boundary asgiref introduces for async views, and every connection attempt fails immediately, whether the pool is opened before `app.run()` or lazily on first request. `web/db.py`'s `PerRequestConnectionSource` opens and closes a single raw connection per request instead (same `async with source.connection() as conn:` calling convention, so `db/queries.py`/`rendering/` callers are unaffected) — confirmed to work reliably across repeated calls, including when the connection is created on a different event loop than the one handling the request (as it will be under pytest-asyncio in tests). Real cost: no connection reuse across requests at all in the web app (unlike the sync worker, which never crosses an `async_to_sync` boundary and keeps its real pool). Acceptable at v1's single-guild scale — a local Postgres connection's setup cost is small relative to query time — but worth knowing if the `<200ms` page-load acceptance criterion is ever measured and missed. |
| `deploy/cdk/`'s `cdk deploy` has never been run against a real AWS account | No AWS account is available in this environment. `npm install && npx cdk synth` is verified clean (zero errors, zero warnings, expected CloudFormation shape spot-checked per stack — see ROADMAP.md §8), but everything downstream of an actual deploy — ALB reachability, ACM certificate validation, the EBS-backed Postgres volume actually attaching and persisting data, `aws ecs run-task` actually succeeding for the one-shot migrate task — is unexercised. Flagged here per this project's own convention rather than left implicit; see `deploy/cdk/README.md`'s own "What's verified, and what isn't" section for the same account in more detail. |
| The setup wizard's atomic `.env` write (`env_file.py`'s `write_env_updates`, mkstemp + `os.replace`) can't rename over the production `web` service's single-file bind mount (`docker-compose.yml`'s `./.env:/app/.env`) — Linux refuses to replace an active mountpoint via rename, surfacing as `EBUSY` or `EXDEV` | `write_env_updates` falls back to a non-atomic in-place write (no rename) when `os.replace` fails with either errno — loses crash-safety in that one topology only, an accepted tradeoff matching this module's existing no-file-locking gap. Unit-tested by simulating both errnos (monkeypatched `os.replace`); **not** verified against a real Docker bind mount, since neither `docker-compose.dev.yml` nor this environment can exercise one — the original compose-stack verification (ROADMAP.md §8) confirmed the wizard serves `/intro` inside the container but never that `finish` completes a write through the real bind mount. Found via a real production crash report, not caught by any prior test. |

**Open questions**

1. Freeform channels: weekly pseudo-topics vs. continuous board view as the default reading mode? (Ship both, instrument nothing, ask the three people who use it.) Leaning continuous-as-default per early usage feedback — tracked as a backlog item in `ROADMAP.md`.
2. Should reactions display per-emoji reactor lists (requires storing user-reaction pairs) or aggregate counts only? Current design says counts only — less data, simpler compliance story.
3. Bucket boundaries for pseudo-topics: calendar weeks vs. activity-gap detection (a lull of >N hours starts a new "topic")? Gap detection reads better but is less predictable for permalinks.

## 11. Alternatives considered

- **Self-hosting Linen:** ~70% of v1 for near-zero effort, but historically weak forum-channel support, a chat-log rather than forum reading model, and no path to the phpBB pagination/date-jump experience that motivated this project. Remains the right answer if the goal is the artifact rather than the project.
- **Thin client (no database):** already validated as an afternoon build; abandoned as the base because search, offset pagination, unread tracking, and user pages all require a local index. The thin client survives as a useful spike and a fallback.
- **Static exports (DiscordChatExporter):** snapshots, not a living interface; wrong tool for this goal though the right one for archival.
