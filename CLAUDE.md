# CLAUDE.md

Guidance for working in this repo. See [`DESIGN.md`](./DESIGN.md) for the full design, [`README.md`](./README.md) for the project overview, and [`ROADMAP.md`](./ROADMAP.md) for v1 build order.

## Stack

- **Python** on the backend, **Jinja** templates for server-side rendering. SSR is the default and preferred approach for every page — it's a deliberate feature of this project (fast paint, retro forum feel), not a starting point to migrate away from.
- **React** is permitted on the client only when a feature genuinely needs client-side interactivity that SSR/Jinja can't reasonably provide. Scope it narrowly — an isolated widget, not a page or app shell. Do not reach for React by default, and do not let it grow into a SPA.

## Testing

- Practice TDD wherever practical: write the failing test first, then the implementation that makes it pass.
- Every feature needs both **unit tests** (e.g. sync worker logic, permission computation, rendering helpers) and **automated end-to-end tests** (e.g. forum pages, search, pagination) — not one or the other.

## Commits

- Never sign or attribute commits to Claude. No `Co-Authored-By: Claude` trailer, and no Claude identity in the commit author field. This overrides Claude Code's default commit-attribution behavior for this repo.
