# Roadmap: v1

v1 scope, in the order it makes sense to build it. Full rationale for each item lives in [`DESIGN.md`](./DESIGN.md); this file tracks build order and progress. Estimate for the whole milestone: **roughly one focused week, plus three days** (§6).

Everything here targets a single Discord server, public (`@everyone`-readable) channels only, membership-gated access. Role-gated channels, permission mirroring, and everything else in the migration path live in `DESIGN.md` §7 and are explicitly out of scope for v1.

## 1. Sync worker (~2–3 days)

- [x] Discord bot connection (discord.py or discord.js), gateway + REST
- [x] Checkpointed initial backfill of in-scope channels and threads (resumable across restarts)
  - [x] Channel message backfill: paginated, checkpointed, idempotent (dedup on rerun), resumable across restarts — unit, integration, and live tested
  - [x] Thread backfill (threads have no permission overwrites of their own — visibility keys off the parent channel)
    - Along the way, fixed a live bug found by inspection (not yet hit in practice): the schema/event-handler plumbing for thread messages already existed, but no code anywhere created a `threads` row — a real thread message would have `ForeignKeyViolation`d on `messages.thread_id` the moment it arrived via gateway. Fixed with `upsert_thread()` + self-healing wiring in `events.handle_message_create/edit`, same pattern as `discover_channels()`.
    - `discover_active_threads()` (one non-paginated `Guild.active_threads()` call, run every `on_ready`) covers active threads; `backfill_guild_threads()` covers archived threads too (`TextChannel.archived_threads(private=False)`, folded into the same walk as backfilling them) — both gated on the parent channel being public+indexed. New `thread_sync_state` table (migration `0003`) checkpoints thread backfill separately from channel `sync_state`, following the `channels`/`threads` precedent of separate tables per concept rather than a shared nullable-pair schema. `backfill_thread()` is a structural twin of `backfill_channel()`, matching the precedent `reconcile_channel()` already set, and shares channel backfill's concurrency semaphore rather than a separate budget.
    - Known, permanent gap (not a bug): private archived threads the bot hasn't joined are invisible without `Manage Threads`, which the sync worker deliberately doesn't request (minimal-permissions design). Documented in `DESIGN.md` §10.
    - Unit, integration, and live tested (`tests/live_discord/test_thread_backfill.py`) — including a live-discovered race (`wait_until_ready()` resolving before `on_ready()`'s discovery actually finishes) and a live-discovered Discord API constraint (webhooks can only auto-create threads in forum channels, so the live tests target one persistent manually-created test thread, `DISCORD_TEST_THREAD_ID`, rather than creating one per run).
  - [x] Forum-channel branch (forum channels have no top-level history; everything lives in threads)
    - A forum "post" is just a `discord.Thread` whose parent is a `discord.ForumChannel` — most of the thread infrastructure above was already forum-agnostic. The actual work was removing three explicit exclusions that predated this item: `discover_channels()` no longer skips computing `is_public` for forum channels; `discover_active_threads()` no longer excludes forum-parented threads; the archived-thread walk (now `backfill.discover_archived_threads()`, extracted from `backfill_guild_threads()` so reconciliation can reuse it too, see below) no longer excludes forum channels either.
    - Found and fixed a real `TypeError` risk by reading the installed discord.py source directly: `ForumChannel.archived_threads()` has no `private=` kwarg at all (forum threads can never be private), unlike `TextChannel.archived_threads()` — calling the wrong shape would have crashed the moment forum channels stopped being excluded. Fixed with an `isinstance(channel, discord.ForumChannel)` branch; the integration test's fake channel subclasses the real `discord.ForumChannel` (not a duck-typed fake) specifically so it would have caught this if the branch were wrong.
    - Found and fixed a second real bug by inspection: `reconcile_guild()`'s per-channel loop only excluded `category`, not `forum` — harmless only while forum's `is_public` was hardcoded false, and would have started wrongly calling `reconcile_channel()` against a forum channel's nonexistent top-level history the moment the above fix landed. Both fixes shipped together; a regression test (fake fetcher that raises if ever called for the forum channel's id) proves it.
    - Unit, integration, and live tested (`tests/live_discord/test_forum_channel.py`): confirms `is_public` computes true for a real forum channel, confirms `archived_threads()` doesn't raise against the real `ForumChannel` object, and confirms a webhook-created forum post (`thread_name=` — which, unlike plain text channels, forum channels actually support) is discovered live via `on_thread_create` and backfilled. Needed a second persistent test fixture: `DISCORD_TEST_FORUM_CHANNEL_ID` plus a webhook bound to it (`DISCORD_TEST_FORUM_WEBHOOK_URL` — webhooks can't post cross-channel, so the existing `#general` webhook couldn't be reused).
- [x] Live event handling: `MESSAGE_CREATE`, `MESSAGE_UPDATE`, `MESSAGE_DELETE`, `MESSAGE_DELETE_BULK`, thread lifecycle, channel updates
  - [x] Message create/edit/delete/bulk-delete — wired into `ThreadbareClient`, unit-equivalent + integration tested; edits reuse the same upsert path as create
  - [x] Full-lifecycle live test (post → edit → delete, verified read back via the sync worker) — see the "Full-lifecycle live testing" item below
  - [x] Thread lifecycle (create/update/delete)
    - `on_thread_create` (reliable for genuine new threads — no cached-vs-uncached ambiguity, since this is the first time the client ever learns of the thread) plus `on_raw_thread_update`/`on_raw_thread_delete` (the *raw* variants specifically — confirmed by tracing discord.py's actual dispatch logic that the cooked `on_thread_update`/`on_thread_delete` only fire for already-cached threads, exactly the unreliability this item was deferred for; the raw variants always fire, mirroring the existing `on_raw_message_edit`-over-`on_message_edit` precedent). New `events.handle_thread_upsert()` (gated on the parent's `should_sync`, since this can fire with no message ever written) and `events.handle_thread_delete()` (→ new `repository.delete_thread()`, cascades to messages/`thread_sync_state` for free).
    - Confirmed via the installed discord.py source that thread visibility changes need no handling here at all: `Thread.permissions_for()` delegates entirely to the parent channel (threads store no permission overwrites of their own), so a thread's visibility is exclusively a parent-channel-permission-change concern, already fully covered by the existing `handle_channel_permissions_changed`/`purge_channel_content` path.
    - Live-testing an actual thread rename/archive/delete turned out to be infeasible without violating the minimal-permissions design: empirically confirmed (`403 Forbidden: Missing Access`) that the sync-worker bot's `View Channels` + `Read Message History` permissions can't edit or delete a thread. `on_thread_create` gets real live coverage via the forum-channel branch's live test (a webhook-created forum post genuinely fires `THREAD_CREATE`); `on_raw_thread_update`/`on_raw_thread_delete` are covered by integration tests only (fake payloads against real Postgres) — a documented, permanent gap in live coverage for those two specifically, not a shortcut.
- [x] Public-channel computation: `channels.is_public` derived from role/channel overwrites, recomputed on `CHANNEL_UPDATE` and role events; content removed from index if a channel stops being public
  - Core logic (`compute_is_public()`, `refresh_channel_public_status()`) and live wiring (`on_guild_channel_update`, `on_guild_role_update`, `on_guild_role_delete`) both done, unit/integration/live tested
- [x] Nightly reconciliation sweep re-walking recent history to repair missed events
  - `reconcile_channel()` re-walks a lookback window and converges local state (upserts repair missed creates/edits, a diff against what's still on Discord repairs missed deletes) — unit, integration (including the exact "kill worker for an hour, restart" drift scenario against real Postgres), and live tested
  - `reconciliation_loop()` runs it immediately on startup (catch-up) then nightly; only touches channels already `is_public`+`indexed`, so it can't accidentally re-add content to a channel that's supposed to be gated
  - Thread reconciliation: `reconcile_thread()` (structural twin of `reconcile_channel()`) plus `reconcile_guild_threads()`, wired into `reconcile_guild()`. Unlike `backfill_guild_threads()` (a one-shot, `on_ready`-guarded startup pass), this re-discovers active *and* archived threads fresh on every nightly sweep — the only recurring mechanism that will ever catch a thread created and archived entirely during a single gateway outage, since a one-shot backfill can't retroactively see it. The archived-thread discovery walk was extracted into a standalone `backfill.discover_archived_threads()` specifically so both backfill and reconciliation could reuse it without duplicating the walk. Runs the actual per-thread reconcile sequentially (not concurrently like backfill) — nightly reconciliation has a full day of slack and nothing downstream blocks on completion time, so the shared-concurrency-budget complexity backfill needed at startup isn't worth reproducing here. Unit and integration tested (including the "thread never seen since last sweep" rediscovery scenario against real Postgres).
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
- [x] Reaction ingestion: `MESSAGE_REACTION_ADD`/`REMOVE`/`REMOVE_ALL`/`REMOVE_EMOJI` → aggregate counts in `reactions` (no per-user reactor identity, per `DESIGN.md` §3/§10's open question)
  - Two complementary write paths, matching the same "live events for speed, reconciliation for correctness" split already used for messages/threads: four new gateway handlers (`on_raw_reaction_add`/`remove`/`clear`/`clear_emoji`, using the raw variants exclusively) do near-real-time increment/decrement/clear via new `repository.py` functions (`increment_reaction`, `decrement_reaction`, `clear_reactions`, `clear_reaction_emoji`); `RepositoryBackfillSink.write_message()` gained a `sync_message_reactions()` call that makes the `reactions` table match `message.reactions` exactly on every backfill pass, reconciliation sweep, and live create/edit — every `Message` object those paths already touch carries Discord's authoritative current counts for free, so this needed zero new reconciliation-specific code and gives backfill accurate historical counts.
  - `handle_reaction_add`/`handle_reaction_remove` are gated on a new `repository.message_exists()` check before writing — a reaction event for a message this instance never stored (outside reconciliation's lookback, or never backfilled) would otherwise raise `ForeignKeyViolation` on `reactions.message_id`.
  - This is also the (originally vacuous) "hard-delete semantics for ... reactions" claim (§2) becoming actually exercised: `decrement_reaction`/`clear_reactions`/`clear_reaction_emoji` are real deletes/decrements against a table that previously had nothing to delete.
  - Live-testing add/remove/clear turned out to be infeasible without widening the project's minimal-permissions design: confirmed Discord webhooks have no reaction capability at all (every other live test in this codebase uses a webhook specifically so the bot never needs write permissions), so exercising this live would need the bot's own token plus a new `Add Reactions` permission — deferred rather than granted this session. A documented, permanent gap in live coverage (see `DESIGN.md` §10), backed instead by integration tests (fakes against real Postgres) covering all four gateway paths plus the `write_message()` sync path.

## 2. Data model (Postgres)

- [x] Core tables: `guilds`, `channels`, `threads`, `users`, `messages`, `attachments`, `reactions`, `sync_state` (§4.1)
- [x] `tsvector`/GIN full-text index on `messages`
- [x] Hard-delete semantics for messages/attachments/reactions (no soft-delete flags)
  - Messages and attachments: confirmed real `DELETE`/`ON DELETE CASCADE` everywhere (`delete_message`, `delete_messages`, `delete_thread`, `purge_channel_content` in `repository.py`), no soft-delete column in any migration. Integration-tested including cascade (`test_purge_channel_content_removes_messages_and_cascades`, `test_delete_thread_removes_the_row_and_cascades`).
  - Reactions: now genuinely exercised, not vacuous — reaction ingestion (§1) landed real decrement/clear/clear-emoji paths against `reactions`, integration-tested (`test_decrement_reaction_deletes_the_row_when_count_reaches_zero`, `test_clear_reactions_removes_all_rows_for_a_message`, `test_clear_reaction_emoji_removes_only_the_given_emoji`).

## 3. Rendering (~1–2 days)

- [x] Discord-flavored markdown rendering (lean on an existing library, accept ~80% fidelity initially)
  - `rendering/markdown.py` parses via `discord-markdown-ast-parser` (chosen over hand-rolling Discord-syntax rules on top of a CommonMark engine, since Discord's markdown isn't CommonMark) and emits HTML, with `nh3` as a defense-in-depth sanitization pass over the HTML this module constructs itself. Bold/italic/underline/strikethrough/inline-code/code-blocks (with language class)/blockquotes render as standard tags; unit tested (`tests/unit/rendering/test_markdown.py`), including an XSS regression test and two documented upstream library quirks matched rather than worked around (a code block's leading newline is dropped but a trailing one is kept; same-character `***bold and italic***` nesting is a known, unfixed parser limitation).
  - Found and fixed a real fidelity gap by reading the installed library's lexer source directly (this project's established practice): its `EMOJI_CUSTOM` regex has no `<a:name:id>` (animated) branch at all, so animated custom emoji fell through as garbled text plus a bogus node. Fixed by normalizing the `a:` prefix away before parsing to get correct parser output.
  - **Real gap found on a live deployment**: the fix above originally hardcoded `animated=False` when rendering the resulting `EMOJI_CUSTOM` node, since the `a:` prefix that signals animated-ness had already been stripped before parsing — so every animated custom emoji rendered as a static `.png`, only the first frame, even though the emoji CDN serves a real animated `.gif` at the same id. Fixed by scanning the raw (pre-normalization) content for animated-emoji ids separately (`_find_animated_emoji_ids`) and threading that id set through rendering instead of a bare `bool`, so the correct extension is chosen per emoji. Reaction-token rendering (`rendering/emoji.py`'s `render_emoji_token_html`) was unaffected — it already parsed the `a:` prefix directly, never routing through markdown.py's node-based renderer.
  - Also handles bare/angle-bracketed URLs (`URL_WITH_PREVIEW`/`URL_WITHOUT_PREVIEW` node types, not originally called out in the plan — found during implementation) as plain links; no distinct preview-card feature exists here since that's `embeds.py`'s job, driven by Discord's own structured embed data.
- [x] Custom emoji, mentions resolved to display names
  - `rendering/emoji.py` renders custom-emoji `<img>` tags off Discord's static, unsigned CDN URL pattern (no expiry/proxy needed, unlike attachments) and handles the `reactions.emoji` string token form. User mentions (`<@id>`/`<@!id>`) resolve against the existing `users` table via a new batched read layer, `db/queries.py` + `rendering/resolve.py` (`build_resolved_refs`) — one query per id-kind for a whole page's worth of messages, not per-mention, so it's ready for §4's pagination without rework. Unresolved users/channels fall back to an inert placeholder rather than erroring. Role mentions (`<@&id>`) always render as an inert placeholder — no `roles` table exists, out of scope here (flagged, not a bug). Unit + integration tested.
  - Custom emoji `<img>` tags (both inline in message content and reaction badges, since both route through the same `render_custom_emoji_html`) gained a `title` attribute alongside `alt`, so hovering shows the emoji's shortcode (e.g. `:star:`) as a native browser tooltip — `alt` alone only surfaces on image-load failure, not on hover.
  - Standard unicode emoji (bare glyphs like 😏) get the same hover tooltip too, matching Discord's own client. This needed a real new dependency (`emoji`, a well-maintained MIT-licensed package) since nothing already in this project maps a unicode glyph back to a shortcode name — added deliberately, not silently, since this project otherwise keeps a lean dependency list. `rendering/emoji.py`'s `unicode_emoji_title` prefers the package's short Discord/Slack-style "alias" (e.g. `:smirk:`) over its longer canonical CLDR name (`:smirking_face:`) when both exist, since the alias usually matches what Discord's own client shows; `render_unicode_text_with_emoji_titles` wraps just the recognized emoji substrings of a text run in `<span title="...">`, leaving the rest as plain escaped text — used for both reaction tokens and unicode emoji embedded directly in message content (the parser library has no distinct node type for those; they're just part of a TEXT node's literal content). Unit, integration, and e2e tested.
  - **Real gap found on a live deployment (screenshot comparison against the real Discord client)**: a bot/webhook message containing a literal `:game_die:` shortcode (bots bypass Discord client-side shortcode-to-unicode conversion) rendered as that literal text — `NodeType.EMOJI_UNICODE_ENCODED` previously always fell back to plain text, on the stated assumption that "no local emoji-name lookup exists." That assumption held until the `emoji` package above was added for tooltips, which also made resolving a shortcode back to unicode straightforward for free (`emoji.emojize`). Fixed via `resolve_shortcode_to_unicode` (`rendering/emoji.py`), consulted before falling back to literal text; a genuinely unrecognized name still renders as literal text, unchanged. Unit and e2e tested (`test_unresolved_shortcode_from_a_bot_renders_as_the_real_emoji`).
- [x] Reply-chain quoting as classic forum quote blocks
  - `rendering/quotes.py`: one-hop only (matches both Discord's own reply-preview UX and `messages.reply_to_id`'s actual shape — a single self-referencing FK, not a chain), quote snippet is escaped plain text (not re-rendered markdown, to avoid truncating mid-token), `data-quoted-message-id` attribute left for §4 to wire into an actual permalink rather than guessing the URL scheme now. Unit + integration tested, including the "target since removed" case.
- [x] Embeds, spoilers, aggregate reaction counts
  - **Real gap found and fixed before this could be built at all**: Discord embeds were never captured anywhere in the sync worker — `discord.Message.embeds` was never read in `backfill.py`/`events.py`/`transform.py`, no `embeds` table existed. Fixed with migration `0004_embeds.sql`, `EmbedLike`-family Protocols in `discord_types.py`, `transform.embed_to_row()`, and `repository.sync_message_embeds()` (delete-then-bulk-insert, since embeds have no stable Discord-side id to upsert against and their count/order can change on edit — same "make it match exactly" self-healing shape as `sync_message_reactions`), wired into the shared `write_message()` sink so backfill/reconciliation/live create-edit all get it for free. Historical embeds on already-backfilled messages are **not** retroactively recovered automatically (would need a full re-backfill per channel at real API cost) — an operator can now trigger that manually via `threadbare-sync-worker --reset-channel <id>` (see UI polish backlog below); documented as a scope cut, not an oversight.
  - `rendering/embeds.py` renders title/description (itself run back through the markdown renderer)/color/author/footer/image/thumbnail/fields; `rendering/attachments.py` handles the `SPOILER_`-filename-prefix convention for spoilered attachments and image-vs-generic-file link shape; spoilered message text renders as a JS-free `<details>/<summary>` disclosure. `rendering/reactions.py` renders the aggregate counts already fully built by §1. All unit tested; `rendering/render_service.py` (`render_message_for_display`) is the single orchestration entry point tying markdown/mentions/quotes/attachments/embeds/reactions together for one message, integration-tested end to end (`tests/integration/rendering/test_render_service.py`) as this milestone's stand-in for e2e — there's no page yet for a browser-driven test to attach to; that lands naturally with §4.
  - **Real gap found on a live deployment**: `rendering/attachments.py`'s image-vs-generic-file check relied solely on `attachments.content_type`, but Discord omits that field for some attachments (older uploads, or detection failures — confirmed by reading discord.py's own source, `content_type: Optional[str] = data.get('content_type')`, added in 1.7, `NotRequired` in Discord's payload type). A real image attachment with a null `content_type` rendered as a plain filename link instead of inline. Fixed with a fallback: when `content_type` is missing, guess from the filename extension via stdlib `mimetypes.guess_type` instead (`filename` is the one field Discord always provides). Unit-tested (`test_render_attachment_html_falls_back_to_extension_when_content_type_missing`).
  - **Real gap found on a live deployment**: an animated-GIF link (e.g. a Tenor/Giphy unfurl) rendered as a static first-frame image. `embed.image`/`embed.thumbnail` are only a static preview frame for these "gifv"-type embeds — the actual animated (often mp4-transcoded) content lives in `embed.video`, a field this project never captured at all (`EmbedLike`'s Protocol had no `video` attribute, `embed_to_row` never read it, no `video_url` column existed). Fixed end to end: `video: EmbedMediaLike | None` added to `EmbedLike` (`discord_types.py`), `embed_to_row` captures `video.url`, migration `0007_embed_video.sql` adds `embeds.video_url`, and `rendering/embeds.py` renders a `<video autoplay loop muted playsinline>` tag in place of (not alongside) the static image when a video URL is present. Unit, integration (round-trip through `sync_message_embeds`/`get_embeds_for_message`), and e2e (`test_gifv_embed_renders_an_autoplaying_video_not_a_static_image`) tested.
  - **Follow-up real gap, found immediately after shipping the above**: a real gifv embed commonly carries its static preview in `thumbnail_url`, not `image_url` — the video-vs-image precedence only guarded against `image_url`, so the `thumbnail_url` block (a separate, unconditional `if`) still rendered alongside the video, showing both. Fixed by gating that block on the same `has_video` check.
  - `scripts/render_preview.py` (dev-only, not in the test suite or CI) renders real mirrored messages to a static HTML file for eyeballing output in a browser ahead of §4 existing; used to manually verify the full pipeline against seeded data during development, catching one real bug (`nh3`'s attribute allowlist silently dropped the `<dl class="embed-fields">` class since `dl` wasn't in the allowed-attributes map) that no unit test had caught — now covered by a regression test.

## 4. Forum web app (~2–3 days)

- [x] Board index: categories/boards with post counts, last-post author/time
  - First milestone with an actual HTTP server: `src/threadbare/web/` (Flask app factory, `flask[async]` views calling the existing async `db/queries.py`/`rendering/` layers directly). No OAuth gate yet — that's §6, after the web app exists, per ROADMAP's build order (DESIGN.md's "site access gated by OAuth" describes v1 as a whole, not this milestone specifically).
  - `db/queries.py` gained `get_boards_and_categories` (categories always shown; boards only if currently `is_public`+`indexed` — non-public content is already purged at the source, so this just keeps a now-content-less row from appearing as a browsable board) and `get_board_post_aggregates` (post count + last-post author/time for a whole batch of boards in one window-function query, not a per-board round trip — combines a board's direct messages with every message inside its threads via `UNION ALL`). `web/board_tree.group_channels_by_category` (pure) groups/orders the result for the template; a board whose category was filtered out upstream (non-public category) folds into "uncategorized" rather than silently vanishing — a real case, not just defensive code, caught while writing the grouping tests.
- [x] Paginated topic/board reading (25 posts/page default), first/prev/next/last, jump-to-date
  - `db/queries.count_messages_before` is the single shared primitive behind pagination, permalinks, and jump-to-date alike: "how many messages precede this point" in a container, with `before=` accepting either a bare date (jump-to-date) or a `(posted_at, id)` tuple (permalink target), or `None` for a total count. `pagination.page_number_for_offset` turns that count into a page number. Both are pure/pure-adjacent and reused identically by `topic.py` and `board.py`'s continuous/weekly views — no duplicated pagination math per view.
- [x] Permalinks per message + "view on Discord" deep link
  - `urls.py` (new top-level module, deliberately *not* under `web/`): pure URL builders callable from `rendering/` without `rendering/` ever depending on `web/` (Flask/Jinja request context). `permalink_for_message` canonicalizes purely on which of `thread_id`/`channel_id` is set (mirrors `messages_container_check`) — a freeform-channel message always resolves to its continuous-view URL, never the weekly view, so there's exactly one canonical permalink per message. This closed two gaps `rendering/quotes.py` and `rendering/attachments.py` deliberately left open in the rendering milestone: reply quotes now carry a real `href` (computed via `count_messages_before` + `permalink_for_message`), and attachment links now route through `/att/{id}` instead of the raw, often-expired `cached_url`.
- [x] Full-text search with author/channel/date-range filters, results link into context
  - `db/queries.search_messages`/`count_search_results`: `websearch_to_tsquery` (Google-style syntax, never raises on malformed input — unlike `to_tsquery`, which matters for a public search box) against the existing `tsv` GIN index. Each result's context-link position (`preceding_count`) is computed inline as a correlated subquery reusing the same `(posted_at, id) <` comparison as `count_messages_before`, so results link into the right page of the right topic/board with the post anchored, not an isolated snippet. Also filters on `channels.indexed = true` — currently a no-op (nothing sets `indexed` false yet, that's §6/§7), added now as cheap forward-compat rather than a retrofit later.
- [x] User pages: display name, avatar, post count, recent posts (indexed content only)
  - `get_post_count_for_user`/`get_recent_posts_for_user`, same `indexed` guard as search. Each recent post can live in a different topic/board (unlike a single paginated view), so its permalink page number is computed per-post rather than shared across the list.
- [x] Freeform-channel handling: both weekly pseudo-topics and continuous paginated view (open question in §10 — ship both, no default judgment yet)
  - `pseudotopics.py` (new top-level module): ISO calendar weeks (`date.isocalendar()`/`fromisocalendar()`, correct across Gregorian year boundaries — e.g. 2025-12-29 is ISO week 1 of 2026), resolving DESIGN.md §10's "calendar weeks vs. gap detection" question in favor of calendar weeks — predictable permalinks matter more than optimal reading grouping. `db/queries.get_weeks_for_board` groups by `extract(isoyear/week from posted_at)`, matching `pseudotopics`' own convention exactly so week ids round-trip. `web/board_tree.board_view_mode` (pure) distinguishes forum/media channels (topics only, no direct messages possible) from text/news channels (topic list *and* continuous/weekly controls, since a text channel can have both direct messages and native Discord threads).
- [x] CSS-custom-property theme contract (markup themed from the start, not retrofitted)
  - `web/static/theme-plain.css`: the full v1 variable set (color roles, typography, spacing, structure) plus `prefers-contrast`/`prefers-reduced-motion` hooks (present now, filled in properly by §5's four themes), applied over stable semantic classed markup (`board-row`, `topic-row`, `post`, `post-meta`, `reply-quote`, ...) shared across every template via `_post.html`/`_pagination.html` partials. Doubles as the "Plain" reference theme §5 will otherwise have needed to build from scratch.
- [x] Attachment proxy endpoint (`/att/{attachment_id}`) with signed-URL refresh + expiry cache
  - `web/discord_rest.py`'s `refresh_attachment_urls` calls Discord's `POST /attachments/refresh-urls`; Postgres's own `url_expires_at` column (with a 5-minute safety margin) *is* the expiry cache DESIGN.md asks for, no separate cache needed. Degrades safely to a 404 "attachment unavailable" page on any failure rather than a 500.
  - **Live-tested against the real test Discord server, not just mocked**: posted an image, backfilled it, forced its local expiry into the past, hit `/att/{id}` against a real running `threadbare-web`. Confirmed live: the endpoint accepts a bot token (no 401) and the response shape matches what `discord_rest.py` implements — the two things `RESOLVED_ISSUES.md` had flagged as unverified are now resolved.
  - **This same live test surfaced a real, separate bug**: `web/db.py`'s `PerRequestConnectionSource` never committed — `conn.close()` on an open transaction discards it — so the attachment-refresh write (and any other web-app write) was silently lost on every request despite a normal 302 response. Invisible to `tests/integration/web/` because its `FakePool` shares one already-open, never-closed connection across a whole test (read-your-own-writes made it "look" committed without ever needing a real commit). Fixed by wrapping the connection in psycopg's `async with conn:` idiom (matching `AsyncConnectionPool.connection()`'s own commit-on-success behavior); `tests/integration/web/test_db.py` now guards this specifically with its own separate verifying connection. See `RESOLVED_ISSUES.md` for the full account, including the lesson for future write paths in `web/`.
  - **Real, unplanned architecture finding**: `psycopg_pool.AsyncConnectionPool` (used everywhere else in this codebase) does not survive Flask's `async_to_sync` bridge — confirmed by direct experimentation, not a guess (its background maintenance tasks get orphaned across the thread/event-loop boundary asgiref introduces, failing every connection attempt immediately). `web/db.py`'s `PerRequestConnectionSource` opens a fresh connection per request instead — same calling convention, so `db/queries.py`/`rendering/` are unaffected, but no connection reuse across web requests. See DESIGN.md §10.
  - **Second, related finding**: pytest-asyncio's `asyncio_mode = "auto"` keeps an event loop alive for the whole session, which breaks Flask's test client the same way (`AsyncToSync` refuses to run inside an already-running loop) — `tests/integration/web/` is consequently the one integration-test package using plain sync test functions with a hand-rolled sync `web_conn` fixture (`asyncio.run()` internally) rather than the shared async `db_conn` rollback fixture used everywhere else. `tests/e2e/` hit a variant of the same issue in its own fixtures (fixed by using synchronous, not async, psycopg for seeding) — both documented in-place with the reasoning, not just worked around silently.
  - Full route coverage (`tests/integration/web/`, Flask test client) plus the project's first real `tests/e2e/` Playwright tests, now that there are actual pages to click through: board index, topic pagination + permalink round-trip, search click-through into context, and a CSS-custom-property-contract smoke check. Manually verified end-to-end against a real running `threadbare-web` process too (not just the test client), including live markdown/mention rendering, permalinks, and Discord deep links.

## 5. Themes (~1 day)

- [x] subSilver-ish (default)
  - `web/static/theme-subsilver.css`: beveled (`outset`/`inset` border-style) posts/inputs, a
    navy-gradient category bar, tiny pagination links, zebra-striped tables — same CSS
    custom-property contract as `theme-plain.css` so `test_css_custom_property_contract_is_present`
    holds regardless of active theme.
  - Cookie-based theme switcher shipped alongside it (not itself a checklist item, but needed
    to make more than one theme reachable): `web/themes.py`'s `resolve_theme()` (query param →
    cookie → default), wired into `app.py` via `before_request`/`context_processor`/`after_request`,
    a `<nav class="theme-switcher">` in `base.html`. Unit, integration, and e2e tested.
- [x] vBulletin dark
  - `web/static/theme-vbulletin-dark.css`: dark charcoal/navy palette, a saturated blue
    gradient header/category bar, rounded (`--radius: 6px`) boxes with thin flat borders —
    deliberately not a subSilver reskin (subSilver is light-by-default with beveled
    `outset`/`inset` rectangles; this is dark-by-default with real border-radius, the actual
    visual fingerprint distinguishing the two forum families). Same CSS custom-property
    contract; needs no changes to the switcher (`web/themes.py`/`app.py`/`base.html` already
    iterate `AVAILABLE_THEMES` generically — confirmed, not assumed, before writing this).
    Unit, integration, and e2e tested.
- [x] Terminal (green-on-black monospace)
  - `web/static/theme-terminal.css`: the most stylistically distinct of the four — `--font-body`
    itself is the monospace stack (every other theme only uses it for `code`/`pre`), near-black
    background, phosphor-green foreground used for both text and links (real terminals don't
    distinguish link color by hue), `--radius: 0`, no gradients/shadows — headers and category
    bars are inverse-video (green background, black text) instead. Same CSS custom-property
    contract; no switcher/app.py/base.html changes needed (confirmed generic, as with vBulletin
    dark). Unit, integration, and e2e tested.
- [x] Plain (reference implementation for future theme authors)
  - Already existed from §4 (`theme-plain.css` doubled as the CSS-variable-contract reference);
    now selectable via the theme switcher above rather than being the only stylesheet.
- [x] `prefers-contrast` and `prefers-reduced-motion` support across all four shipped themes
  - Present in `theme-plain.css`, `theme-subsilver.css`, `theme-vbulletin-dark.css`, and
    `theme-terminal.css` — all four themes in v1's scope (§6) are now shipped.

## 6. Access control (~1 day)

- [x] Discord OAuth (`identify` + `guilds` scopes) login gate — any guild member may read
  - Hand-rolled (not a library) in `web/discord_rest.py` (`exchange_oauth_code`,
    `get_current_user`, `get_current_user_guilds`, sharing a new `DiscordRestError` base with
    the existing attachment-refresh error) and `web/views/auth.py` (`/login`, `/oauth/callback`,
    `/logout`). Flask's built-in signed-cookie session stores only `user_id`/`display_name`/
    `is_mod` — never the OAuth token. Login is rejected entirely (not just admin access) for
    anyone who isn't a member of `DISCORD_TEST_GUILD_ID`. `web/app.py`'s global `before_request`
    gate covers every existing route for free. Unit, integration, and e2e tested (the e2e tier
    seeds a signed session cookie directly rather than faking a full Discord OAuth double — see
    `tests/e2e/conftest.py`'s `LiveServer.session_cookie`).
- [x] Mod admin page: per-channel indexing toggle, sync health view
  - Mod = Manage Server or Administrator on the mirrored guild, read from the OAuth
    `guilds`-scope `permissions` field (`web/authz.py`'s `has_mod_permissions`) — no separate
    per-role lookup needed. `db/admin_queries.py` (new, deliberately separate from the
    read-only, member-safe `db/queries.py`) adds `set_channel_indexed` plus reads against
    `sync_state`/`worker_heartbeat` (previously sync-worker-internal), with a 5-minute
    heartbeat-staleness threshold. `web/views/admin.py` + `admin.html`. Unit, integration, and
    e2e tested.
  - **Trigger re-backfill from the admin page deliberately deferred**, not built: the web app and
    sync worker are separate processes with no IPC today (no LISTEN/NOTIFY, no RPC, no polling
    flag), and bolting that on was explicitly scoped out this round rather than rushed. The admin
    page renders no such control (guarded by a regression test asserting its absence) — follow-up
    work, not a gap discovered late. A CLI equivalent (`threadbare-sync-worker --reset-channel
    <id>` / `--reset-all-channels`, see UI polish backlog below) now covers the same need without
    needing that IPC — it resets the channel's (and its threads') stored checkpoint, then a normal
    sync-worker restart does the real re-walk through the existing, already-tested backfill path.

## 7. Setup wizard (~1 day)

- [x] First-run detection (unconfigured install serves the wizard, not the forum)
  - `config.is_configured()`/`get_database_url()` (purely additive; `load_settings()` itself
    untouched, so the sync worker's own boot behavior is unaffected). `web/cli.py`'s `main()`
    branches on it: normal path is byte-for-byte what it was before; unconfigured path builds
    `web/wizard_app.py`'s standalone mini Flask app (not a blueprint on `create_app()`, since
    that factory and its `before_request` hooks assume a fully populated `Settings`) wrapped in
    a new `web/app_switcher.py::AppSwitcher` — a mutable WSGI dispatcher that lets the process
    drop out of wizard mode into the real forum app in place once `.env` is written, with no
    restart. Proven end-to-end by an e2e test that finishes the wizard and then confirms `/`
    is served by the real app's login gate afterward.
- [x] Guided bot-creation walkthrough — **text + numbered steps, not screenshots** (a deliberate
  call: authentic Discord developer-portal screenshots aren't something this pass could
  produce, and stale placeholder images seemed worse than clear numbered copy reusing
  `DEVELOPMENT.md`'s already-battle-tested portal-navigation wording verbatim).
- [x] Bot invite URL generation (`bot` scope, minimal permissions: `View Channels` + `Read Message History`)
  - `wizard/invite.py::build_invite_url`, reusing `discord_permissions.REQUIRED_PERMISSIONS`
    (66560) rather than a second hand-derived value. The guild isn't asked for by hand (which
    would require enabling Developer Mode) — the wizard auto-detects which guild the bot landed
    in via a bot-token `GET /users/@me/guilds` call once the mod confirms the invite.
- [x] Preflight checks (§8.2): Message Content intent, per-channel permission verification, OAuth redirect URI round-trip, token shape/identity validation
  - `wizard/preflight.py::compute_bot_effective_permissions`/`compute_channel_permission_table`
    resolve the *bot's own* effective permissions per channel (base → category overwrites →
    channel overwrites, with an Administrator short-circuit), distinguishing "guild-level grant
    insufficient" from "a specific overwrite denies the bot" per DESIGN.md §8.2 — the
    correctness-critical core of this feature, given the most thorough fixture coverage in the
    milestone. Token shape/identity via a new bot-token `GET /users/@me` call
    (`discord_rest.get_bot_user`). The OAuth redirect URI round-trip is the *real* thing, not a
    synthetic check: the wizard's `/oauth/callback` route is registered at the same literal path
    `auth_bp` uses in production, so the URI a mod registers in the developer portal never needs
    to change once the wizard hands off.
- [x] Channel list with indexing toggles, computed public/gated status
  - **"Estimated message counts" deliberately dropped**: no per-channel count query exists
    anywhere in this codebase, Discord's REST API has no such endpoint, and computing one would
    mean paginating full history — exactly what the wizard exists to avoid before a mod has
    confirmed the indexed set. Copy instead points at the admin page once backfill completes.
- [x] Resumable wizard state (safe to abandon and re-run)
  - New singleton `wizard_state` table (migration `0005`, `worker_heartbeat`'s proven
    singleton-row idiom) persists every *non-secret* value collected mid-wizard (client ID,
    redirect URI, guild ID, channel confirmations) — resilient to a completely abandoned and
    later-resumed session. Deliberately stores **no secrets**: the bot token and OAuth client
    secret live only in the wizard's ephemeral Flask session between steps, so losing that
    session (a restarted web process, a closed tab) only ever costs re-pasting those two
    values, never redoing the guided walkthrough, the bot invite, or channel confirmations —
    `wizard/steps.py::resolve_resume_step` is what notices a secret went missing and bounces a
    request back to whichever step re-collects it. E2e tested for both ordinary
    bookmark/back-button resume and this session-loss scenario specifically (a real redirect-
    loop bug was found and fixed while writing that test — see the code review for detail).
  - **"Re-run the wizard" against an already-configured install is explicitly out of scope** —
    the user asked for this to be deferred pending a decision on what should authorize it (the
    same Manage Server check `/admin/` uses? something stricter, since it can rewrite
    `.env`/secrets?). `wizard_state` is never deleted and `is_configured()` still exists, so
    adding a real entry point later is additive, not a redesign, but no route/button exists yet.
- [x] Generated mod-facing pitch kit (what's stored, how deletions propagate, admin page link)
  - `/pitch-kit` — pure template, no new data beyond what DESIGN.md §8.3 already specifies.
- [x] `/oauth` step: hide the client-secret form once already submitted this session
  - Found on a real deployment: the client-secret `<form>` (`web/templates/wizard_oauth.html`)
    rendered unconditionally on every GET, so it stayed visible alongside the "Test login" link
    even after the secret had already been saved to the session. Fixed: `oauth()` now computes
    `has_client_secret`/`show_secret_form` (reusing the existing `"client_secret" in session`
    expression `wizard/steps.py::resolve_resume_step` already relies on, rather than inventing a
    new check) and passes both to the template on every GET/POST branch; the template gates the
    form behind `show_secret_form` and shows a "Client secret saved. Change it" link
    (`?edit=1`) in its place otherwise. Unit/integration tested (`test_wizard_oauth_step.py`).

## 8. Deployment (~1–1.5 days, CDK severable as its own 0.5 day)

- [x] Docker Compose stack: web, sync worker, Postgres, Caddy (TLS via Let's Encrypt)
  - Root `Dockerfile`: one shared multi-stage image (uv-based, `python:3.12-slim`) for
    `migrate`/`web`/`sync-worker` — same package, different `command:` per service, no reason
    for separate images. `docker-compose.yml` (distinct from the existing dev-only
    `docker-compose.dev.yml`): `postgres` (no published port — internal network only, per
    DESIGN.md §8.4's explicit gotcha), `migrate` (one-shot, `depends_on: service_healthy`),
    `web`, `sync-worker` (both `depends_on: service_completed_successfully` on `migrate`),
    `caddy` (`caddy:2-alpine`, ports 80/443, automatic Let's Encrypt via a root `Caddyfile`
    reverse-proxying to `web:5000`).
  - `web/cli.py` gained a `HOST` env var (default `127.0.0.1`, unchanged for bare local `uv run
    threadbare-web`) — a real code fix, not just config: inside a container the app must bind
    `0.0.0.0` or Caddy (a separate container) can never reach it. The compose file sets
    `HOST=0.0.0.0` for the `web` service only. Unit tested.
  - `web`'s compose service bind-mounts `./.env:/app/.env` **read-write** (not just
    `env_file:`) — required because the setup wizard's `write_env_updates()` (§7) must persist
    its writes back to the *host* filesystem so a later `docker compose restart sync-worker`
    picks them up; `env_file:` alone only populates env vars at container start, it isn't a
    live file the container can write through.
  - This single-file bind mount turned out to be incompatible with `write_env_updates()`'s
    atomic rename (Linux refuses to `rename()` over an active mountpoint) — a real production
    crash, since the local verification below only confirmed the wizard serves `/intro` inside
    the container, never that `finish` completes a write through the real bind mount. Fixed
    with a fallback in-place write; documented gap, not a bug left unaddressed — see
    `DESIGN.md` §10.
  - `sync-worker` didn't get a matching bind mount at first — only `env_file:` — so the
    `docker compose restart sync-worker` this same section (and the wizard's own finish page)
    tells operators to run never actually delivered the wizard's config: found on a real
    production deployment as an empty channel list. Fixed by giving `sync-worker` the same
    `./.env:/app/.env` mount, read-only (it never writes the file). See `RESOLVED_ISSUES.md`.
  - Build and boot verified directly: image builds cleanly, both `threadbare-migrate` and the
    other entrypoints import and run inside the container, `docker compose config` validates,
    and a full local run (`postgres`/`migrate`/`web`/`sync-worker`, no real domain/Caddy needed
    for this part) confirms the wizard serves `/intro` on a fresh, unconfigured `.env`, and —
    using this project's own real test-bot credentials — the sync worker actually connects to
    Discord's live gateway from inside the container.
  - **Not exercised**: Caddy's real Let's Encrypt handshake, which needs a real domain + public
    DNS. Flagged as an untested-in-practice gap rather than silently assumed to work, matching
    this project's convention (see DESIGN.md §10's other flagged gaps).
- [x] Option A docs: self-host (Tailscale / Cloudflare Tunnel guidance)
  - New `### Option A` section in `README.md`, same compose stack as Option B — only the
    reachability guidance differs (Tailscale for a handful of trusted users, Cloudflare Tunnel
    for public reachability without port-forwarding, classic port-forward/dynamic DNS
    documented but discouraged since a churning residential IP breaks the OAuth redirect URI).
- [x] Option B docs: VPS (recommended default) — provision → Docker → DNS → done
  - New `## Deployment` section in `README.md`: provision Ubuntu LTS → install Docker + Compose
    → clone → `cp .env.example .env` (fill in `POSTGRES_PASSWORD`/`THREADBARE_DOMAIN` only —
    everything Discord-specific comes from the wizard) → point DNS → `docker compose up -d` →
    visit the domain. Gotchas called out explicitly per DESIGN.md §8.4: unattended security
    upgrades, Postgres staying internal-only, a VPS snapshot as a stopgap for the (deferred)
    config backup job, and updating the OAuth redirect URI if the domain ever changes.
  - **2026-07-22 revisit**: this content was expanded into a beginner-friendly walkthrough (SSH
    basics, what a DNS `A` record is and where to add one, firewall/security-group ports,
    concrete `unattended-upgrades` commands, a troubleshooting section) and moved to
    [`docs/self-hosting.md`](./docs/self-hosting.md), since the original README prose assumed
    the reader already knew VPS/DNS/reverse-proxy concepts. `README.md`'s `## Deployment` section
    now holds only a short summary + link for Options A/B; Option C is untouched.
- [x] Option C: `deploy/cdk/` TypeScript CDK app (Fargate ×2, ALB+ACM for web only, Postgres sidecar w/ EBS, RDS as commented-out alt)
  - `deploy/cdk/` (TypeScript, `aws-cdk-lib` v2): `NetworkStack` (public-subnet-only VPC,
    `natGateways: 0`), `DatabaseStack` (Postgres on Fargate + a 20GB EBS volume via
    `ecs.ServiceManagedVolume`, RDS sketched as a commented-out alternative), `WebStack` (ALB +
    ACM + Fargate via `ApplicationLoadBalancedFargateService`), `SyncWorkerStack`
    (`desiredCount: 1`, no load balancer at all, zero-inbound security group), and
    `MigrateStack` (a one-shot `threadbare-migrate` task definition, not in DESIGN.md's literal
    bullet list but added since the deployment can't function without it — mirrors
    `docker-compose.yml`'s one-shot `migrate` service).
  - **The setup wizard doesn't apply to this path, documented explicitly**: Fargate tasks share
    no filesystem, so the wizard's `.env`-bind-mount hand-off (Options A/B) has nothing to write
    to across separate `web`/`sync-worker` tasks. Discord config instead comes from an
    operator-created `threadbare/app-config` Secrets Manager secret, populated before first
    deploy — both tasks start already configured. See `deploy/cdk/README.md`.
  - Two operator-provided secrets (`threadbare/database`, `threadbare/app-config`) rather than
    CDK auto-generating and composing a `DATABASE_URL` from a separately-generated Postgres
    password — avoids `SecretValue.unsafeUnwrap()`'s string-interpolation escape hatch, which
    the CDK docs themselves discourage when avoidable. Documented as a deliberate simplicity
    tradeoff, not a missing feature.
  - **Verified**: `npm install && npx cdk synth` succeeds with zero errors and zero warnings,
    producing the expected CloudFormation shape for all five stacks (spot-checked directly:
    ALB/listener/target-group/service shape in `ThreadbareWeb`; correct
    `command`/`secrets`/`environment` per task definition; `DesiredCount: 1` and no load-balancer
    resources at all in `ThreadbareSyncWorker`).
  - **Not verified, and explicitly flagged rather than assumed**: a real `cdk deploy` against an
    AWS account — none is available in this environment. ALB reachability, ACM validation, and
    the EBS volume actually attaching/persisting are unexercised. Recorded in `DESIGN.md` §10.
- [ ] Nightly dump of Threadbare-native tables only (mod config, setup state) — no message
      backups — **deferred**, by explicit user choice, as its own follow-up (backup script +
      cron mechanism + retention pruning); the VPS docs note a manual VPS snapshot as a stopgap
      in the meantime.
- [x] Production web-server revisit: gunicorn + a restart-on-finish wizard hand-off
  - The compose stack previously ran `web` via Werkzeug's built-in dev server (single process)
    because the setup wizard's `AppSwitcher` hot-swapped the running Flask app in-process once
    `.env` was written — that only worked within a single long-running process, and a
    multi-worker server forks separate OS processes the hot-swap couldn't reach.
  - `web/cli.py` now launches gunicorn (via its documented `BaseApplication` custom-application
    recipe, loading the already-built Flask app object directly) for the configured branch,
    with worker count from a new `WEB_CONCURRENCY` env var (default 4) and the port itself now
    overridable via a new `PORT` env var (needed for testing — the hardcoded production port,
    5000, isn't reliably bindable in tests, per this project's own established note about macOS
    AirPlay Receiver squatting it). `AppSwitcher` is deleted entirely (`web/app_switcher.py` and
    its test) — nothing hot-swaps in-process anymore.
  - New hand-off: the wizard's `on_complete` schedules a short-delayed `os._exit(0)` (giving the
    "All set" response time to reach the browser) instead of swapping an app object. Docker
    Compose's existing `restart: unless-stopped` policy on the `web` service brings the
    container back up; `main()` re-checks `config.is_configured()`, now true, and takes the
    gunicorn branch. Bare local `uv run threadbare-web` has no such restart policy — a developer
    finishing the wizard there has to rerun the command themselves, an accepted tradeoff for a
    one-time setup flow.
  - `wizard_finish.html` now explains the self-restart and includes a JS-free meta-refresh back
    to `/`, matching this project's no-unnecessary-JS convention (same idiom as the spoiler
    `<details>` disclosure in §3).
  - Tests: unit tests for the gunicorn wrapper class and the on_complete restart-scheduling
    behavior; the e2e wizard-completion test now proves only the wizard's own half of the
    hand-off (writes `.env`, invokes `on_complete`, shows restart messaging) since the
    in-process app-swap it used to assert on is gone. A new, genuinely-subprocess e2e test
    (`tests/e2e/test_web_process_restart.py`) proves the other half for real — a real
    `threadbare-web` process, started fresh against an already-configured environment, serves
    the real forum app's login gate via gunicorn (not an in-thread fake). What neither test
    proves (deliberately, not a gap in disguise): that Docker Compose's `restart:
    unless-stopped` policy itself actually restarts a container after `os._exit(0)` —
    reimplementing Docker's own supervision behavior in the test harness wasn't worth it.
    That link was verified manually instead: a real `docker compose build && up` (isolated
    Compose project, the existing dev stack untouched) showed gunicorn's real boot banner (4
    worker PIDs), and sending `SIGTERM` to the container's PID 1 showed Compose bringing it
    back up with a fresh boot banner and worker set within ~2 seconds.
- [x] `install.sh`: a one-command installer for Options A/B, automating the manual
      `cp .env.example .env` → edit → `docker compose up -d` walkthrough that
      `docs/self-hosting.md` currently spells out by hand. Prompts for the site's URL and parses
      it into `THREADBARE_DOMAIN` plus an optional subpath, rewriting the `Caddyfile`'s
      `redir`/`handle_path`/`header_up` block only if a subpath was given (see "Running at a
      subpath" in `docs/self-hosting.md`) — a root deployment leaves the shipped `Caddyfile`
      untouched. Generates a random `POSTGRES_PASSWORD` (via `openssl rand -hex`, so it's always
      URL-safe once interpolated into `DATABASE_URL`) and writes a `.env` with just that and
      `THREADBARE_DOMAIN` populated (everything Discord-specific still comes from the setup
      wizard afterward, unchanged), then runs `docker compose up -d`. Fails fast with a clear
      message on missing prerequisites (Docker/Compose not installed, port 80/443 already
      bound, an `.env` that already exists) rather than a confusing failure partway through.
      `scripts/install.sh`, matching `scripts/upgrade.sh`'s location and style. Verified manually
      against a real, isolated (`-p`-namespaced) Docker Compose stack in both the root-domain and
      subpath branches (`.env` contents, Caddyfile rewrite, `docker compose config`/`up -d`
      all confirmed) — not automated-tested, since it's shell orchestration over real
      infrastructure, same convention as `scripts/upgrade.sh` and the rest of this project's
      live-only verifications. Docs updated: `docs/self-hosting.md` and `README.md` both point
      at it as a shortcut over the fully manual walkthrough.

## 9. Upgrade path (~0.5–1 day)

A new, final v1 item, added by explicit user request after §8: before v1 is "complete," an
operator running a real instance needs a documented, at-least-partially-enforced way to
upgrade to whatever ships next (`DESIGN.md` §7's migration-path phases), not manual data
surgery. Full "contract shape" is in `DESIGN.md` §7's new "Upgrade contract" subsection; this
tracks what got built to back it.

- [x] Schema-compatibility startup check (the core enforcement mechanism)
  - `db/migrate.py::check_schema_up_to_date()` — a read-only sibling of `run_migrations()`,
    reusing the same `discover_migrations()`/`_ensure_schema_migrations_table()`/
    `_applied_migrations()`/`pending_migrations()` machinery — raises `MigrationError` if any
    migration the running code ships with hasn't been applied yet.
  - Wired into both `web/cli.py::main()` (both the configured and wizard branches, right after
    the DSN is known) and `sync_worker/cli.py::_run()` (as the first line). `MigrationError` is
    caught at the top level and turned into a clear stderr message plus `SystemExit(1)`, same
    shape as the existing `ConfigError` handling.
  - This protects all three deployment paths uniformly: a no-op for Compose in the normal case
    (the `migrate` service already runs first via `depends_on`), the real safety net for Option
    C operators who forget `aws ecs run-task`, and a genuine dev-UX improvement for bare local
    `uv run threadbare-web`/`threadbare-sync-worker` without having run `threadbare-migrate`
    first (previously a confusing failure, now a clear one).
  - Unit + integration tested (`tests/integration/db/test_migrate.py`,
    `tests/integration/web/test_cli.py`, `tests/integration/sync_worker/test_cli.py`).
- [x] Version exposure
  - `threadbare/__init__.py`: `__version__` from installed package metadata
    (`importlib.metadata.version`) — works today since `uv sync` already installs real
    metadata, confirmed rather than assumed.
  - `--version` on all three CLI entry points (`threadbare-migrate`, `threadbare-web`,
    `threadbare-sync-worker`), short-circuiting before any config/DB access — unit tested.
  - Mod admin page (`db/admin_queries.py::get_latest_migration_version` +
    `web/views/admin.py` + `admin.html`): a new "Version" section showing the running app
    version and the latest applied migration — the concrete way an operator confirms an
    upgrade actually took effect. Integration tested.
- [x] Upgrade scripts, one per deployment path
  - `scripts/upgrade.sh` (Options A/B): clean-tree check → `git fetch`/`pull --ff-only` →
    `docker compose build` → `docker compose up -d` (migrations apply automatically via the
    existing `depends_on` gate) → tails the migrate log. Verified manually against a real,
    isolated (`-p`-namespaced) Docker Compose stack — not automated-tested, since it's shell
    orchestration over real infrastructure, same convention as the rest of this project's
    live-only verifications.
  - `deploy/cdk/upgrade.sh` (Option C): `cdk deploy --all` (forwarding whatever `-c` context
    flags are passed) then automatically fetches and runs `ThreadbareMigrate`'s
    `RunTaskCommand` CloudFormation output — closes the "operator must remember to re-run
    migrate" gap `deploy/cdk/README.md` previously documented as manual-only. Verified via
    `bash -n` plus confirming `cdk synth` still emits the `RunTaskCommand` output it depends
    on; not deployable/verifiable here (no AWS account), same documented gap as the rest of
    Option C.
- [x] Documentation: `DESIGN.md` §7's new "Upgrade contract" subsection (the six hard rules
      plus the recommended, not-yet-acted-on, version-bump/tag release convention),
      `README.md`'s per-deployment-option "Upgrading" guidance pointing at the two scripts.

## v1 acceptance criteria

Pulled from `DESIGN.md` §6 — the milestone isn't done until these hold:

- [ ] A million-message channel backfills unattended (resumable across restarts), then browses at server-side page-load times under 200ms
- [ ] Killing the sync worker for an hour and restarting produces a fully consistent mirror after the next reconciliation pass
- [ ] Deleting a message on Discord removes it from Threadbare within seconds via gateway; reconciliation catches gateway-outage deletions within 24h
- [ ] A channel switched from public to role-gated disappears from the index automatically

## After v1

Not this milestone — tracked in `DESIGN.md` §7 for when/if they happen: role-gated channels with permission mirroring (Phase 2, next up — broken into build order below), reading-experience depth like unread tracking (Phase 3), public/logged-out access (Phase 4), multi-guild hosting (Phase 5), other chat platforms (Phase 6).

## Phase 2: Role-gated channels with permission mirroring (~3–5 days, `DESIGN.md` §7)

The defining feature of "full": index non-public channels and show each logged-in user exactly what they can see on Discord. Build order below front-loads the data/plumbing work, since every later step depends on roles and overwrites actually being in Postgres first.

- [ ] New `roles` table (id, guild_id, name, permissions, position) and per-channel permission-overwrite tables (role tier + member tier), captured by the sync worker on `GUILD_ROLE_CREATE`/`UPDATE`/`DELETE` and `CHANNEL_UPDATE` — neither is captured anywhere today; `discord_permissions.compute_is_public` and `wizard/preflight.py` both currently read overwrites live off Discord's in-memory objects rather than storing them.
- [ ] Persist each member's current role-ID list. `sync_worker/events.handle_member_update` (already shipped for display-name refresh, reusing the `GUILD_MEMBERS` intent — see UI polish backlog above) only reacts to *future* role changes; Phase 2 also needs an initial bulk backfill of every existing member's roles on startup, since that handler alone never populates a member who hasn't changed since the intent was added.
- [ ] Generalize Discord's permission-resolution order (base @everyone → role allows/denies → category overwrite → channel overwrite → admin short-circuit) into one shared implementation, rather than a third reimplementation next to the two narrow cases that already exist: `discord_permissions.compute_is_public` (the @everyone-only case) and `wizard/preflight.py`'s `_apply_overwrite_tier`/`compute_bot_effective_permissions` (the bot-only case). Both should end up calling the shared version.
- [ ] Compute a per-user channel-visibility set from stored roles/overwrites at login (and refresh on a timer plus the role/channel-update events above); cache it per session. This replaces `web/authz.py`'s current binary is-a-guild-member gate for channels enrolled in role-gating.
- [ ] Filter every read path by the requesting user's visibility set, not just global `is_public`/`indexed` — board index, topic/continuous listings, and especially search (today's `db/queries._SEARCH_WHERE_SQL` filters only on `indexed`, with no per-user clause at all) all need a new join/parameter. The easiest bypass vector in the whole phase, per `DESIGN.md` §7's risk note.
- [ ] Per-channel opt-in flag on the admin page, separate from the existing `is_public`/`indexed` toggle — mods enroll a role-gated channel into the new visibility system deliberately, never automatically.
- [ ] Golden/fixture-based permission tests exported from a real test server, covering the actual resolution edge cases (explicit deny overrides allow, category-vs-channel precedence, multiple roles combined, admin short-circuit). The highest test-coverage bar in the codebase, per `DESIGN.md` §7 — this is the one place a bug is a disclosure bug, not a rendering bug.

## UI polish backlog

Small, unscheduled fixes/improvements surfaced from using the app. Not phase-scoped — pick up opportunistically.

- [x] Page `<title>` and visible page header should show the Discord server's name (e.g. "General (threadbare view)") instead of a generic/static title.
  - `web/app.py` gained an async `before_request` hook (`resolve_site_title`) that looks up `guilds.name` via a new `db/queries.get_guild` and stores `f"{name} (threadbare view)"` on `g.site_title` (falling back to `"Threadbare"` if no guild row exists yet), exposed to every template via the existing theme context processor. `base.html`'s header link and default `<title>` block, plus every child template's `{% block title %}` override, now read `{{ site_title }}` instead of the literal `Threadbare`. One extra trivial single-row-PK query per request, on the same no-connection-pooling cost model `web/db.py` already accepts — no caching, so a guild rename shows up immediately.
- [x] Default a channel's view to continuous browsing rather than weekly pseudo-topics; keep the weekly toggle available alongside it. Resolves `DESIGN.md` §10 open question 1 in favor of continuous-as-default.
  - `board_landing` (`/board/<id>`) is now a thin dispatcher matching the existing `board_continuous_index` redirect idiom: a freeform (text/news) channel redirects to continuous page 1, a topics_only (forum/media) channel redirects to a new `/board/<id>/topics` route (the topic-list rendering moved there unchanged). `board_continuous.html` (shared by the continuous and weekly views) gained the same "Browse continuously / Browse by week / View topics list" nav `board_topic_list.html` already had, factored into a shared `_freeform_controls.html` partial.
  - The "page list" part of this item turned out to already exist: `board_topic_list.html` already includes `_pagination.html`. The one remaining gap — the top-level board/channel index has no pagination at all — is low-value at typical server channel counts and was left alone rather than guessed at; still open if it turns out to matter.
- [x] Images in rendered posts should auto-scale down to fit the viewport instead of rendering at full original size.
  - Each theme's existing `.attachment img { max-width: 100%; ... }` rule gained `max-height: 80vh;` — with no explicit width/height on the `<img>`, browsers scale to satisfy both constraints while preserving aspect ratio, no markup/JS change needed. Regression-guarded by a new `tests/unit/web/test_theme_css.py` (asserts the rule exists across all four stylesheets) plus an e2e assertion in `test_forum_smoke.py`.
- [x] Show each poster's avatar on their posts (matching classic forum layouts); include an option (user- or site-level) to disable avatar display.
  - New `rendering/avatars.py` (pure, mirrors `rendering/emoji.py`'s no-proxy-needed shape) builds the URL directly from `users.avatar_hash`, which was already captured by the sync worker but never selected for render — `db/queries._MESSAGE_COLUMNS_SQL` now includes it, propagating to every message-row query that already joins `users`. Falls back to Discord's current (post-username-migration) default-avatar formula, `(user_id >> 22) % 6`, for members with no custom avatar.
  - The toggle is a cookie, not a per-user DB setting (per explicit direction — see the new "Display preferences" backlog item below and `DESIGN.md` §6): new `web/preferences.py` mirrors `web/themes.py`'s exact `resolve_*`/before_request/context_processor/after_request shape for a `show_avatars` cookie + `?avatars=on|off` query param, defaulting to shown. `_post.html` and `user.html` (completing `DESIGN.md` §5 feature 7's user-page avatar, spec'd but never wired up) render a small inline `.post-avatar`/`.user-avatar` `<img>` in the existing flex `.post-meta` row rather than a restructured phpBB-style two-column layout — the latter is a bigger, separable piece of visual work across all four themes if wanted later.
- [x] Discord member-join/leave system messages currently render as empty posts — render real system-message text instead of hiding/omitting the rows.
  - Compared hiding vs. showing before implementing: hiding turned out to be the riskier option, since it would need a `type` exclusion filter threaded consistently through six separate counting/pagination functions in `db/queries.py` to keep page counts matching what's displayed — an easy place for drift. Showing real text needs a schema/ingestion change but then these are just ordinary posts with real content, so no query-layer changes were needed at all.
  - New `messages.type smallint NOT NULL DEFAULT 0` column (migration `0006_message_type.sql`), captured from discord.py's `Message.type` in `transform.message_to_row` (`getattr` with a fallback rather than a required Protocol field, specifically so the ~8 other test files with their own duplicated `FakeMessage` fakes didn't all need updating). Unlike `posted_at`/`author_id`, `type` **is** updated on conflict in `repository.upsert_message` — it was never captured before this migration, so every pre-existing row started out wrong, and only re-including it in `ON CONFLICT DO UPDATE` makes a re-backfill an actual repair path (a first pass at this excluded it, matching the immutable-on-conflict convention by default, then corrected once the re-backfill CLI item below made the mistake concrete — see that item's caveat for how this data is actually recovered on an already-synced install).
  - New `rendering/system_messages.py` (pure, no I/O) mirrors a deliberately-scoped subset of discord.py's own `Message.system_content` (verified against the installed library source directly, `discord/message.py:2681-2866`) — new-member joins (including Discord's own deterministic 13-message welcome rotation, keyed by timestamp), pins, boosts/tiers, channel renames, thread-created notices, etc. Types needing data this project never captures (calls, purchases, polls, role subscriptions, stage channels, who got added/removed from a thread) fall back to a generic notice rather than fabricating fidelity we can't have. `render_message_for_display` routes the synthesized text back through the existing markdown renderer (for the `**bold**` markers Discord's own strings already contain) with empty `ResolvedRefs`, since `system_content` uses plain author names, never `<@id>` mention tokens — confirmed by reading the source, so no mention-resolution query was needed. Skips the reply-quote/attachment/embed/reaction queries entirely for system messages (structurally they never have any).
  - `_post.html`/theme CSS: system-message posts get a `post-content-system` modifier class (italic, `--color-fg-muted`) reusing existing shared custom properties, no new tokens.
  - Unit tests (`test_system_messages.py`, extended `test_transform.py`), integration tests (`test_render_service.py`, `test_repository.py`), and an e2e test (`test_forum_smoke.py`) all added.
- [x] CLI command to force a re-backfill of a channel — closes the gap the system-messages item above (and, before it, the embeds item in §3) both flagged: historical rows keep whatever the sync worker captured at the time and are never retroactively corrected on their own.
  - Discovery that shaped the design: `backfill_guild()` already re-walks *every* in-scope channel on *every* sync-worker startup — cheap no-op for channels already fully synced, since it just asks Discord for "anything after checkpoint X" and gets an empty page back. So the fix doesn't need to reimplement backfill orchestration at all, only reset the stored checkpoint back to "never backfilled" — the next normal startup does the real work through the existing, already-tested path. Two-step operator flow, not one command: the reset itself is a pure DB op (no Discord connection), the actual re-fetch happens on the next `docker compose restart sync-worker`.
  - `threadbare-sync-worker --reset-channel <id>` / `--reset-all-channels`, following this codebase's existing flat `"flag" in sys.argv` CLI style (no argparse/click anywhere) rather than introducing a subcommand parser for one feature. Resets both the channel-level `sync_state` checkpoint and every thread under it in `thread_sync_state` (`repository.reset_thread_checkpoints_for_channel`, new) — a channel-only reset would silently leave every thread under it still marked complete/stale. `--reset-all-channels` excludes category channels (`channel_types.CATEGORY`, same constant `db/queries.py` already uses) since they have no content/checkpoint of their own.
  - Deliberately not an admin-page button: closes the same gap §6's admin page explicitly deferred ("no IPC between the web app and sync worker today") without needing that IPC, since this runs directly against the sync worker's own process/container instead.
  - Unit tests for flag parsing (`test_cli.py`), integration tests for the new repository functions and for `_run_reset` end-to-end against real Postgres (a real committing connection, not the shared rollback fixture, since `_run_reset` opens its own pool — same pattern `test_backfill*.py` already established). Manually verified against the real local dev database too: seeded a stale checkpoint, ran `--reset-channel`, confirmed both the channel's and its thread's checkpoints came back `NULL`/`false`; separately confirmed `--reset-all-channels` reset every non-category channel and `--reset-channel <unknown-id>` fails clearly rather than silently no-op'ing.
  - Documented in `docs/self-hosting.md`'s new "Forcing a re-backfill" section (two-step command, where to find a channel id, that it's rate-limited/slow-ish on a large channel).
- [x] Reflect Discord display-name changes (global username or per-server nickname) in Threadbare.
  - Partially already true: `sync_worker/repository.upsert_user()` overwrites `users.display_name` on every message write — backfill, live create/edit, and reconciliation all share that path (`RepositoryBackfillSink.write_message()`), and `display_name` is read live off `message.author` (a `discord.Member` in a guild context, whose `.display_name` already resolves to the per-server nickname when one is set — no separate nickname field needed). So a rename is already reflected site-wide the moment that member's next message is created, edited, or swept up by reconciliation, since `users` is one row per id, not a per-message snapshot.
  - Real gap (found via a live bug report — a user renamed on the live server before running a backfill and the archived name didn't update): a member who renames but doesn't post/edit anything afterward kept a stale `display_name` indefinitely — nothing re-fetched member profiles independent of message activity. Fixed with a `GUILD_MEMBER_UPDATE` gateway handler: `sync_worker/bot.py`'s `ThreadbareClient` now requests the privileged `GUILD_MEMBERS` intent (a deliberate widening beyond the minimal-permissions design of §3/§8.2, accepted here since there's no cheaper alternative) and its new `on_member_update` delegates to `sync_worker/events.handle_member_update`, which diffs `transform.user_to_row(before)` against `user_to_row(after)` and calls the existing `upsert_user` only when something actually changed — avoiding a write on every unrelated member-update event (roles, timeout, pending status) a busy server generates constantly.
  - Operators must enable "Server Members Intent" on the Bot tab in the Discord Developer Portal — documented in the setup wizard (`wizard_intro.html`) and `DEVELOPMENT.md`'s test-bot checklist, alongside the existing Message Content intent step. No preflight check for it (unlike Message Content's `message_content_intent_ok`) — there's no cheap after-the-fact signal that the intent is missing short of observing a real member update fire, so this was a deliberate scope cut, not an oversight.
  - Unit tests (`tests/unit/sync_worker/test_events.py`, new file) cover the diff/no-op guard in isolation; integration tests (`tests/integration/sync_worker/test_events.py`) cover both the update-existing-row and insert-a-new-row cases against real Postgres — the latter is the exact reported scenario (a member renamed before ever posting, so no prior `users` row exists).
  - No live-gateway test coverage for `GUILD_MEMBER_UPDATE` itself: every `tests/live_discord/` scenario posts via a webhook (`DEVELOPMENT.md`), which has no member identity to rename — see `DESIGN.md` §10's risk table for the same gap already accepted for reactions.
- [x] Consolidate theme, avatar visibility, and posts-per-page onto a single `/preferences` page linked from the masthead, removing the inline toggles previously scattered across `base.html` and every post-listing template.
  - No backend/storage changes: all three stayed exactly as cookie-backed as before. Made possible entirely by `_theme_switch_url`/`_avatar_toggle_url`/`_posts_per_page_switch_url` (`web/app.py`) already building their target URL generically from `request.endpoint`/`request.view_args`/`request.args` rather than hardcoding "return to the page you were on" — rendering them on a dedicated page instead of inline just means clicking one now redirects back to `/preferences` itself, for free.
  - New `web/views/preferences.py` (a thin view, sibling to the pre-existing pure-logic `web/preferences.py`) and `preferences.html`, reusing the existing `theme-switcher`/`avatar-toggle`/`posts-per-page-switcher` class names so all four themes' CSS kept applying unchanged. One small addition: a `.preference-current` class (bold, matching `.pagination-current`'s already-established convention) marks the active theme/page-size option as plain text rather than a link.
- [ ] Migrate cookie-based display preferences (theme, avatar visibility, posts-per-page) to real DB-backed per-user preferences once logged in, instead of a per-browser cookie forever.
  - Deliberately deferred rather than built alongside avatars — see `DESIGN.md` §6's "Display preferences (planned migration)" note. `web/views/auth.py`'s OAuth callback already fetches the logged-in member's current Discord identity on every login and is the natural point to also upsert a preferences row; a new `user_preferences` table would need its own key on the session's `user_id` rather than a FK onto `users`, since a logged-in member who's never posted has no `users` row (same population gap as the display-name-refresh item above). The new `/preferences` page (above) is the natural place such a migration would slot into, unchanged from the reader's perspective.
- [x] Voice (and stage) channels should never be discoverable or toggleable — already a stated non-goal (`DESIGN.md` §2), now actually enforced. `channel_types.py` gained `VOICE`/`STAGE_VOICE` constants plus a shared `NON_CONTENT_TYPES` set (categories + voice + stage-voice). Two worlds, two idioms, matching this codebase's existing split: `sync_worker/discovery.py::discover_channels` (live `discord.py` objects) now excludes voice/stage from the "others" bucket entirely — they get no `channels` row at all going forward, unlike categories, since nothing parents off a voice channel; `backfill.py::SKIPPED_CHANNEL_TYPES` also excludes them, as defense-in-depth for an already-deployed install with a stale row from before this shipped. Every plain-dict-row read site got the same defense-in-depth treatment against that same stale-row scenario: `sync_worker/repository.get_content_channel_ids`, `db/admin_queries.get_channels_with_sync_state`, `db/queries.get_boards_and_categories`, `web/board_tree.group_channels_by_category`, and `web/views/board._get_board_or_404` (a direct URL guess at a voice channel's id now 404s) all swapped their `!= CATEGORY`/`== CATEGORY` check for `NON_CONTENT_TYPES`. `web/views/wizard.py`'s channel-list step (previously two raw `== 4` magic-number checks, not even using the `CATEGORY` constant) now excludes voice/stage too, so a freshly-run wizard never offers one as a toggle. Unit + integration tested at every touched site. Whether a voice channel's text-chat sub-feature should ever be indexed if it turns out to hold real content is a separate, open question, left open — see `DESIGN.md` §10.
- [x] Show each poster's Discord roles and bot/human status, matching Discord's own client: username text colored by the member's highest-position colored role, a small "BOT" badge for bot accounts, and a full role-badge list plus bot badge on the user's profile page (click-through detail).
  - New `roles` table (migration `0008_user_roles.sql`: id/guild_id/name/color/position) plus `users.is_bot`/`users.role_ids` (a plain array, not a join table — the only query need is "this member's current role-id list", never "which members hold role X", matching how `DESIGN.md`'s Phase 2 section already frames this same future data need). Captured by `sync_worker/transform.py::user_to_row`/`role_to_row`, a new `discover_roles()` (sibling to `discover_channels`, wired into `on_ready`), and a new `on_guild_role_create` handler plus extending the existing `on_guild_role_update`/`on_guild_role_delete` to also keep the `roles` table itself in sync (previously those only recomputed channel `is_public`). `handle_member_update` needed zero changes — it already diffs the full `user_to_row()` dict, so it started catching role-list changes for free the moment that dict grew a `role_ids` key.
  - Verified against installed discord.py source rather than guessed: `Member.colour`'s real algorithm (highest-position role among those with a non-zero color) is exactly what a new correlated subquery in `db/queries.py::_MESSAGE_COLUMNS_SQL` computes per message row (`WHERE r.color != 0 ORDER BY r.position DESC LIMIT 1`) — deliberately not cached on `users` at write time, since a role's color changing would then need to fan out to every member holding that role, whereas the live subquery is always correct against current `roles` state for free.
  - New pure `rendering/user_display.py::role_color_hex` (mirrors `rendering/avatars.py`'s style) is this codebase's first inline `style=` in a template (`_post.html`, `user.html`) — Discord role colors are arbitrary per-user RGB and can't be pre-baked into the CSS-custom-property theme contract the way every other color is. All four themes gained `.bot-badge`/`.user-role-badge` rules reusing each theme's own existing custom properties, regression-guarded by `tests/unit/web/test_theme_css.py`.
  - A real bug caught before it shipped: a role with no custom color has `color = 0` (Discord's own "no color" sentinel), and `role_color_hex` initially treated only `None` as "no color" — an uncolored role on the user page's badge list would have rendered as literal black text. Fixed by treating `0` the same as `None`.
  - Unit, integration, and e2e tested (colored-username and bot-badge rendering, the highest-position-colored-role-wins algorithm specifically, `@everyone` excluded from a member's stored `role_ids`). Manually verified in a real browser across two themes (screenshots) per this project's UI-change convention.
- [x] Sync channel metadata (name/topic/position) live, and reflect channel create/delete without waiting for the next reconnect.
  - `discovery.py`'s private `_row_for` was promoted to a public `transform.channel_to_row` (matching `role_to_row`/`thread_to_row`'s existing shape) so both the batch-discovery path and the new live handlers below share one row-builder — `discover_channels` was updated to use it, `_row_for` deleted.
  - New `events.handle_channel_upsert`, wired into the existing `on_guild_channel_update` (previously only recomputed `is_public`, never refreshed metadata): keeps name/topic/position current the moment a mod edits them on Discord, the same way message edits and username changes already sync live. Self-heals the parent category's row first if missing (a mod can create a category and move a channel into it in two separate gateway events) — the same FK-ordering hazard `discover_channels` itself hit once, just live instead of at batch-discovery time.
  - New `on_guild_channel_delete` → `events.handle_channel_delete` → `repository.delete_channel`: a real hard delete: messages/threads/sync_state cascade (`ON DELETE CASCADE`); a deleted *category*'s former children are uncategorized, not deleted (`ON DELETE SET NULL`), matching Discord's own behavior.
  - New `on_guild_channel_create` → `events.handle_channel_create`: makes a brand-new channel appear in the admin panel immediately (so a mod can review it), but deliberately does **not** auto-import it — `repository.insert_new_channel` always inserts with `indexed=false` regardless of the table's normal schema-default-true `INSERT` (see `upsert_channel`), a mod must explicitly flip the existing per-channel "toggle indexed" admin control before any content is fetched. `is_public` is still computed immediately (via the existing `handle_channel_permissions_changed`) so the admin table shows accurate public/private status even while indexing stays off. This closes half of the "auto-index newly discovered channels" gap noted below — the live-create path now defaults to *not* auto-indexing; `discover_channels`'s own batch-reconnect path (a channel created entirely while the bot was offline) is untouched and still defaults `indexed=true` on a fresh row, since changing that default wasn't asked for and is a separate, larger behavior change for an already-deployed install to reason about.
  - Unit tests for `channel_to_row` (`test_transform.py`); integration tests for all three new event handlers against real Postgres (`test_events.py`), including the category self-heal, voice/stage-voice exclusion, and the `ON CONFLICT DO NOTHING` duplicate-event case for channel creation.
  - No new live-Discord test: expect the same permission wall `tests/live_discord`'s thread-lifecycle tests already hit — the sync worker bot's minimal read-only permissions can't create/rename/delete a real channel to trigger these events live. A known, permanent live-coverage gap, not a shortcut, matching how that same limitation is already documented for thread rename/archive/delete above.
- [x] Admin option to auto-index newly discovered channels by default (permissions allowing), so mods aren't stuck hand-toggling every new channel as it appears. The live `CHANNEL_CREATE` path already always defaults to `indexed=false` (see above) — this item was about the *other* path: `discovery.discover_channels`'s batch reconnect scan, which previously always defaulted a genuinely-new row to `indexed=true` at the schema level with no way to configure or disable that.
  - New singleton `site_settings` table (migration `0009_site_settings.sql`, same `worker_heartbeat`/`wizard_state` idiom), one column so far: `auto_index_new_channels boolean NOT NULL DEFAULT true` — no seed row inserted, both new read functions fall back to `true` (today's behavior) until a mod actually flips the toggle.
  - `repository.upsert_channel` gained an `indexed: bool = True` keyword param, included in the INSERT's column list but deliberately absent from `ON CONFLICT DO UPDATE SET` — so it only ever affects a genuinely fresh row, never an already-known channel's mod-set value (locked in by the pre-existing `test_discover_channels_does_not_clobber_indexed_on_rediscovery`, which needed no changes to keep passing). `discovery.discover_channels` reads the new setting once per call and threads it through for its own content-channel loop only — every other `upsert_channel` call site (category rows, both category self-heals in `events.py`) keeps the unchanged `indexed=True` default, since this item is scoped to the batch-reconnect path specifically.
  - `db/admin_queries.py` gained a matching read plus `set_auto_index_new_channels` (insert-or-update the singleton row); `web/views/admin.py` gained a `toggle_auto_index` route mirroring `toggle_indexed`'s exact shape; `admin.html` gained a small "Settings" section with the current on/off state and a toggle button.
  - Unit/integration tested (`test_repository_channels.py`, `test_discovery.py`, `test_admin.py`); manually verified in a real browser across two themes (screenshots) — toggling flips the label and button text and round-trips correctly.
