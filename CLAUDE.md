# CLAUDE.md

Guidance for working in this repo. See [`DESIGN.md`](./DESIGN.md) for the full design, [`README.md`](./README.md) for the project overview, [`ROADMAP.md`](./ROADMAP.md) for v1 build order, and [`RESOLVED_ISSUES.md`](./RESOLVED_ISSUES.md) for a log of real bugs found and fixed.

## Stack

- **Python** on the backend, **Jinja** templates for server-side rendering. SSR is the default and preferred approach for every page — it's a deliberate feature of this project (fast paint, retro forum feel), not a starting point to migrate away from.
- **React** is permitted on the client only when a feature genuinely needs client-side interactivity that SSR/Jinja can't reasonably provide. Scope it narrowly — an isolated widget, not a page or app shell. Do not reach for React by default, and do not let it grow into a SPA.

## Themes

The four `theme-*.css` files in `src/threadbare/web/static/` are held to a **higher readability bar than the rest of the codebase**. They are not just styling — they are the only worked examples a third-party theme author gets, and uploaded `.zip` bundles are validated against the `:root` contract they demonstrate. Assume the reader is competent but not fluent in CSS.

- **`theme-plain.css` is the reference.** It documents how theming works, the required/optional `:root` contract, and what each section is for. The other three defer to it and describe only their own deviations — don't restate the shared contract four times, it will drift.
- **Keep the four parallel.** Same numbered sections, same order, same names, so they can be read side by side. Enforced by `tests/unit/web/test_theme_css.py`.
- **Write comments as rules, not as history.** "Don't paint visited rows `--color-bg-alt`, because it's also the stripe colour and `:nth-child(even)` outranks you" — not "visited rows *were* invisible before we fixed it". A theme author wasn't there for the bug.
- **Explain any rule that exists for a non-obvious reason** — a specificity fight, a scoping guard, a value that must stay in step with another. If a rule looks arbitrary, it will be "cleaned up" by someone later.
- Changes to theme CSS need a **manual visual check in all four themes**. The tests catch missing and drifted rules; they cannot catch valid CSS that renders wrong, which is what every theme bug found so far has been.

## Testing

- Practice TDD wherever practical: write the failing test first, then the implementation that makes it pass.
- Every feature needs both **unit tests** (e.g. sync worker logic, permission computation, rendering helpers) and **automated end-to-end tests** (e.g. forum pages, search, pagination) — not one or the other.

## Commits

- Never sign or attribute commits to Claude. No `Co-Authored-By: Claude` trailer, and no Claude identity in the commit author field. This overrides Claude Code's default commit-attribution behavior for this repo.
