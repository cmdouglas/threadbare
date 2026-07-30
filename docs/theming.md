# Writing a Threadbare theme

Threadbare is themeable by design: the whole look of the forum is one stylesheet, and a mod can
install a new one from the admin page without touching the server. This guide is for people
writing that stylesheet. For *installing* one someone else wrote, see
[Custom themes](./self-hosting.md#custom-themes) in the self-hosting docs.

You need to be comfortable reading CSS. You do not need to know anything about Python, Jinja, or
how Threadbare is built.

## What a theme is

A theme is **one CSS file plus optional media**. Threadbare renders one fixed set of semantic
class names — `.board-row`, `.topic-row`, `.post`, `.post-meta`, `.reply-quote`, and so on — and
the templates are identical no matter which theme is active. A theme never ships markup or
JavaScript. You restyle what the pages already emit; you never change what they emit.

That constraint is what makes themes safe to install and hard to break: a theme can't invent a
page, can't remove a control, and can't leave a reader stuck.

## Start from a built-in

Don't start from an empty file. Go to **Admin → Themes**, download any of the four built-ins, and
edit it:

| Theme | Character |
| --- | --- |
| **Plain** | The reference implementation. Documents how theming works, section by section. Start here. |
| **subSilver** | The 2004 phpBB experience — beveled gradients, dense tables, tiny pagination links. The default. |
| **vBulletin dark** | The other half of forum nostalgia: dark charcoal/navy, saturated blue gradients, rounded boxes. |
| **Terminal** | Green-on-black monospace BBS mode. The most structurally distinct of the four. |

[`theme-plain.css`](../src/threadbare/web/static/theme-plain.css) is the one to read first. It is
deliberately the plainest of the four — no gradients, no bevels, no ornament — so what's left is
exactly the structure every theme has to cover and nothing else. Its comments explain each
section and call out the rules that exist for non-obvious reasons.

All four built-ins carry **the same ten sections, in the same order, with the same names**, so
they can be read side by side: "how does Terminal handle posts?" is answered by opening section 6
of that file. A unit test keeps them aligned. Keep the numbering in your own theme and the next
person gets the same affordance.

| Section | Covers |
| --- | --- |
| 1. Theme palette | The `:root` custom-property contract — most of a theme lives here |
| 2. User preferences | Dark mode, high contrast, reduced motion |
| 3. Base elements | `body`, links, headings, tables |
| 4. Site chrome | Masthead, breadcrumbs, search box, account nav |
| 5. Listing tables | Board index, topic lists, weekly archive; read/unread/visited state |
| 6. Posts | `.post` and its meta line, avatars, badges, the reply-tree scaffolding |
| 7. Post content | Markdown output, mentions, emoji, embeds, attachments, reactions, spoilers |
| 8. Pagination and preference controls | Page links, jump-to-date, theme/avatar/per-page switchers |
| 9. Forms and code | Inputs, buttons, `code`/`pre` |
| 10. Admin and wizard states | Mod-only pages and setup flow |

## The `:root` contract

Every rule in the built-ins is written in terms of custom properties declared in section 1.
**Override that block alone and you already have a complete, coherent theme** — sections 2–10 are
where a theme spends its personality, and you only need the ones you actually want to change.

These are required. An uploaded bundle that omits one is rejected, with the missing ones named:

**Colour**

| Property | Role |
| --- | --- |
| `--color-bg` | Page background |
| `--color-bg-alt` | Secondary surface: category headings, zebra stripes, quote blocks |
| `--color-fg` | Body text |
| `--color-fg-muted` | Timestamps, post counts, secondary metadata |
| `--color-border` | Every rule, divider, and box edge |
| `--color-link` | Unvisited links |
| `--color-link-visited` | Visited links — real signal on a forum, don't collapse it into `--color-link` |
| `--color-accent` | Header/category bars, active states |
| `--color-accent-fg` | Text drawn *on* `--color-accent`; must stay legible against it |
| `--color-danger` | Errors, warnings, destructive admin buttons |

**Typography**

| Property | Role |
| --- | --- |
| `--font-body` | The body font stack |
| `--font-mono` | `code` and `pre` (Terminal makes this the body font too) |
| `--font-size-base` | Base size |
| `--font-size-small` | Metadata, pagination, secondary text |
| `--line-height-base` | Base line height |

**Spacing and structure**

| Property | Role |
| --- | --- |
| `--space-xs` / `--space-sm` / `--space-md` / `--space-lg` | The spacing scale; used for all padding and gaps |
| `--radius` | Corner radius (`0` for a squared-off theme) |
| `--border-width` | Border thickness |
| `--container-max-width` | Page content width |

Two more are **optional**. Each has a `var()` fallback where it's used, so omitting one degrades
rather than breaks:

- `--color-bg-visited` — background of an already-visited board/topic row. Falls back to
  `--color-bg-alt`. See the specificity warning below before you skip it.
- `--embed-color` — **never declare a value for this.** Threadbare sets it inline on each embed
  from Discord's own embed colour. A theme only supplies the fallback, at the use site in section
  7, for embeds that have no colour of their own. Declaring it in `:root` overrides every embed's
  real colour with yours.

If this list and the validator ever disagree, the validator is right:
[`theme_bundle.REQUIRED_CUSTOM_PROPERTIES`](../src/threadbare/theme_bundle.py) is the
authoritative set, and a test keeps this document in step with it.

## Things that will bite you

Each of these was a real bug in a built-in theme.

- **Don't paint visited rows `--color-bg-alt`.** In themes that zebra-stripe, that's the stripe
  colour, and `:nth-child(even)` outranks a plain class selector — so a visited row painted
  `--color-bg-alt` is overridden outright on even rows and indistinguishable from an ordinary
  stripe on odd ones. Use `--color-bg-visited`.
- **If you repaint the header, repaint its links too.** A dark masthead with default link colours
  is the classic unreadable-nav bug. A test enforces this for the built-ins.
- **Style the states, not just the shapes.** `.board-row-unread`, `.topic-row-unread`,
  `.board-row-visited`, `.topic-row-visited`, `.unread-dot`, and `.unread-count` are most of what
  makes a forum index usable. Bolding the row's name link is the classic unread treatment, and it
  survives a reader who can't distinguish the dot's colour.
- **Style the preference controls.** `.theme-switcher`, `.avatar-toggle`,
  `.posts-per-page-switcher`, and `.current-option` are easy to forget and strand the reader on
  the `/preferences` page.
- **Scope row-name rules to `td`.** `.board-name` appears on `<th>` as well, so an unscoped rule
  catches the column header too.
- **Cap media.** `.attachment-image`, `.embed-image`, and `.embed-video` carry arbitrary
  user-posted dimensions; constrain height as well as width or one tall screenshot owns the page.
- **Content is arbitrary.** Titles, display names, and topic names come from Discord and can be
  any length, any script, or emoji. Clamp rather than assume.

## User preferences

Section 2 of every built-in re-declares the palette inside media queries. That's the whole
mechanism — no other rule needs to know a dark or high-contrast mode exists:

```css
@media (prefers-color-scheme: dark) { :root { /* re-declare colours */ } }
@media (prefers-contrast: more)     { :root { --color-border: currentColor; } }
@media (prefers-reduced-motion: reduce) { * { animation-duration: 0.001ms !important; } }
```

A theme that's already dark by default still wants the first block; `theme-vbulletin-dark.css`
explains how the dark-by-default themes handle it. Threadbare ships no animation of its own, but
if your theme adds some, honour the reduced-motion preference.

## Packaging a bundle

A theme is uploaded as a single `.zip`:

```
my-theme.zip
├── theme.css        required — the stylesheet
├── theme.json       optional — {"display_name": "My Theme", "author": "...", "version": "..."}
└── assets/          optional — media referenced by theme.css
    ├── background.png
    ├── heading.woff2
    └── theme-song.mp3
```

Reference media by **relative path** — `background: url(assets/background.png)`. Bundles are
served so that relative paths resolve as-is, with no rewriting on your part.

**Allowed asset types**: images (`png`, `jpg`, `jpeg`, `gif`, `webp`, `avif`), fonts (`woff`,
`woff2`, `ttf`, `otf`), audio (`mp3`, `ogg`, `wav`, `m4a`, `aac`, `flac`), and video (`mp4`,
`webm`, `ogv`).

**SVG, HTML, and JavaScript are deliberately rejected.** Assets are served from the site's own
origin, and those types can execute script on direct navigation — a same-origin XSS surface. This
is a security boundary, not an oversight; there's no flag to turn it off.

**Limits**: 50MB per bundle (100MB per file, 200MB uncompressed, 1000 entries). Yes, you can ship
a background video and a theme song. Consider your readers' data plans.

### What the validator checks

Upload is validated in full *before* anything touches disk, and failures re-render the page with
the specific problems named:

| Rejected | Why |
| --- | --- |
| No top-level `theme.css` | The one required file |
| Missing required custom properties | Named individually in the error |
| `url(...)` or `@import` pointing at a file not in the bundle | Catches typos and case mismatches up front — filesystems on your laptop and the server may disagree about case |
| A disallowed file extension | The media allowlist above |
| Absolute paths, `..`, or symlinks in the zip | Zip-slip |
| Oversized bundles, files, or entry counts | Zip bombs |
| A name colliding with a built-in slug | Pick a different name |

Referencing an external `http(s)` URL is a **warning**, not an error: it works, but the bundle
stops being self-contained and your theme breaks when that host does. `data:` URIs are
self-contained, so they pass silently.

## Installing, replacing, removing

From **Admin → Themes** (mods only):

- **Register** — upload the `.zip`. The theme's name comes from the form field, else
  `theme.json`'s `display_name`, else the filename. It's slugified for the URL.
- **Replace** — register again under the same name. It overwrites in place, so readers keep their
  selection.
- **Delete** — removes the row and the extracted files. Anyone currently using it silently falls
  back to the default theme rather than seeing an unstyled page.
- **Download** — any theme, including built-ins (as raw `.css`) and installed customs (re-zipped).

Registered themes then appear in the switcher on `/preferences` like any built-in.

## Testing your theme

There is no theme-preview mode, so the honest workflow is: install it on a real Threadbare with
real mirrored content, and click through it.

The pages worth checking, because they're where themes actually break:

1. **Board index** — with visited, unvisited, and unread rows all present.
2. **A busy channel** — long posts, images, embeds, reactions, and the reaction filter bar.
3. **A topic in tree view** — deep reply nesting is where indentation and borders fall apart.
4. **Search results** — a distinct layout that's easy to leave unstyled.
5. **`/preferences`** — every switcher, with the current option marked.
6. **A wide table on a narrow window** — the pagination bar and admin tables are the first to
   overflow.
7. **Dark mode and high contrast**, if you support them — toggle at the OS level.

If you're contributing a theme back to Threadbare itself rather than installing your own, note
that the built-ins are held to a higher readability bar than the rest of the codebase and are
covered by [`tests/unit/web/test_theme_css.py`](../tests/unit/web/test_theme_css.py) — see the
Themes section of [`CLAUDE.md`](../CLAUDE.md).

## Operational notes for mods

- **Themes live on a volume, not in Postgres.** Bundles are extracted onto the
  `threadbare-themes` Docker volume. A database dump alone won't restore them — add that volume
  to your backup routine.
- **A theme is code that runs on every page.** Only mods can register one, and only from bundles
  you'd trust the way you'd trust any other code you run. CSS can't execute script here, but it
  can absolutely hide a moderation control or make a page unreadable.
