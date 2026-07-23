#!/usr/bin/env bash
# One-command installer for Options A/B (docs/self-hosting.md) -- automates
# the manual `cp .env.example .env` -> edit -> `docker compose up -d`
# walkthrough those docs currently spell out by hand. Prompts for the site's
# URL, writes .env with a random POSTGRES_PASSWORD and the parsed
# THREADBARE_DOMAIN (everything Discord-specific still comes from the setup
# wizard afterward, unchanged), rewrites the Caddyfile's routing block only
# if a subpath was given (see "Running at a subpath" in
# docs/self-hosting.md -- a root deployment leaves the shipped Caddyfile
# untouched), then runs `docker compose up -d`.
#
# Refuses to touch an existing .env rather than silently overwriting a real
# deployment's config -- this is a first-run installer, not a reset tool.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# ---- fail-fast prerequisite checks ----

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. See https://docs.docker.com/engine/install/, then re-run this script." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed, but this user can't talk to the Docker daemon (permission denied, or the daemon isn't running)." >&2
  echo "Either re-run this script with sudo, or add yourself to the docker group and log back in:" >&2
  echo "  sudo usermod -aG docker \$USER && newgrp docker" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "The Docker Compose plugin isn't available ('docker compose version' failed)." >&2
  echo "See https://docs.docker.com/compose/install/, then re-run this script." >&2
  exit 1
fi

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

for port in 80 443; do
  if port_in_use "$port"; then
    echo "Port $port is already in use -- Caddy needs it free to serve the site and request its TLS certificate." >&2
    echo "Find what's using it (e.g. 'sudo ss -ltnp | grep :$port') and free it, then re-run this script." >&2
    exit 1
  fi
done

if [ -f .env ]; then
  echo ".env already exists -- this looks like an existing install. Remove or rename it first if you want a fresh one." >&2
  exit 1
fi

# ---- prompt for the site URL, parse into domain + optional subpath ----

read -rp "Site URL this install will be reachable at (e.g. https://forum.example.com or https://example.com/discord-mirror): " site_url

domain="$site_url"
domain="${domain#http://}"
domain="${domain#https://}"
domain="${domain%/}"

subpath=""
if [[ "$domain" == */* ]]; then
  subpath="/${domain#*/}"
  domain="${domain%%/*}"
fi

if [ -z "$domain" ]; then
  echo "Couldn't parse a domain out of '$site_url'." >&2
  exit 1
fi

# ---- write .env ----

postgres_password="$(openssl rand -hex 24)"

cp .env.example .env
sed -i.bak \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${postgres_password}|" \
  -e "s|^THREADBARE_DOMAIN=.*|THREADBARE_DOMAIN=${domain}|" \
  .env
rm -f .env.bak

echo "==> Wrote .env (POSTGRES_PASSWORD generated, THREADBARE_DOMAIN=$domain)"

# ---- rewrite Caddyfile only if a subpath was given ----

if [ -n "$subpath" ]; then
  cat > Caddyfile <<EOF
# Reverse proxy + automatic TLS (Let's Encrypt) for the web app container,
# serving Threadbare under the ${subpath} subpath -- see "Running at a
# subpath" in docs/self-hosting.md. THREADBARE_DOMAIN comes from .env. DNS
# must point at this box before first \`docker compose up\`, or the ACME
# HTTP challenge on port 80 will fail.
{\$THREADBARE_DOMAIN} {
	redir ${subpath} ${subpath}/
	handle_path ${subpath}/* {
		reverse_proxy web:5000 {
			header_up X-Forwarded-Prefix ${subpath}
		}
	}
}
EOF
  echo "==> Rewrote Caddyfile for subpath $subpath"
else
  echo "==> Root domain deployment -- Caddyfile left unchanged"
fi

# ---- bring the stack up ----

echo "==> Starting the stack..."
docker compose up -d

echo
echo "==> Done. Once DNS has propagated, visit https://${domain}${subpath}/ --"
echo "    the setup wizard takes it from there."
echo "    Once the wizard finishes, run 'docker compose restart sync-worker' once by hand."
