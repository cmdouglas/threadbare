#!/usr/bin/env bash
# Upgrade a running Docker Compose deployment (Options A/B, README.md's
# Deployment section) to whatever's on the current branch: pull, rebuild,
# and restart. Migrations run automatically -- docker-compose.yml's `web`/
# `sync-worker` services already depend_on the one-shot `migrate` service
# completing successfully before they start, so `docker compose up -d`
# alone re-applies any new migrations before serving a single request.
#
# Safe to run repeatedly (idempotent): a clean tree with nothing new to
# pull is a no-op past the `git pull` step.
#
# See DESIGN.md §7's "Upgrade contract" for the guarantees this relies on
# (additive-only migrations, config backward-compatibility, the app
# refusing to boot on a stale schema rather than misbehaving silently).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Caddyfile is excluded: install.sh rewrites it in place for subpath
# deployments (see "Running at a subpath" in docs/self-hosting.md), and the
# "Running at a subpath" manual-edit path does the same -- either way, a
# subpath deployment's Caddyfile is expected to permanently differ from the
# tracked default, not a sign of unrelated uncommitted work. If the shipped
# default Caddyfile itself ever changes upstream while yours is locally
# edited, `git pull` below will surface a normal conflict to resolve by hand
# rather than silently discarding your routing config.
if [ -n "$(git status --porcelain -- . ':!Caddyfile')" ]; then
  echo "Working tree has uncommitted changes (other than Caddyfile) -- aborting. Commit, stash, or discard them first." >&2
  exit 1
fi

echo "==> Fetching latest..."
git fetch origin

echo "==> Fast-forwarding to origin/$(git rev-parse --abbrev-ref HEAD)..."
git pull --ff-only

echo "==> Building images..."
docker compose build

echo "==> Restarting the stack (migrate runs automatically before web/sync-worker start)..."
docker compose up -d

echo "==> Migration log:"
docker compose logs --no-color --tail 20 migrate

echo "==> Remember to check the admin page's Version section (/admin/) once it's up,"
echo "    to confirm the running version and latest applied migration match what you expect."
