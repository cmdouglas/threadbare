# Self-hosting Threadbare

This is the step-by-step version of [`README.md`](../README.md)'s Deployment section, written
for admins who haven't run a server before. It covers **Option A** (your own hardware) and
**Option B** (a small VPS — recommended for most people). If you already know your way around
AWS, skip straight to [Option C](../deploy/cdk/README.md) instead — it isn't covered here.

Both options run the exact same `docker-compose.yml` stack (web app, sync worker, Postgres,
and [Caddy](https://caddyserver.com/) as a reverse proxy) — the only real difference between
them is how the outside world reaches the box.

## Before you start

A few terms and tools come up repeatedly below. If you already know these, skip ahead.

- **VPS (Virtual Private Server)** — a small Linux machine you rent by the month from a cloud
  provider (Hetzner, DigitalOcean, Vultr, etc.). You get root access over SSH, as if it were a
  physical computer sitting somewhere else. This is what Option B uses.
- **SSH** — how you get a command-line session on a remote machine. From a Mac/Linux terminal
  (or Windows Terminal / PowerShell on recent Windows), it's `ssh root@<the box's public IP>`.
  Most providers let you either upload an SSH key when creating the box (recommended — no
  password to leak) or email you a root password to use on first login.
- **DNS `A` record** — the piece of internet plumbing that makes `forum.example.com` point at
  your box's IP address. You add this in your **domain registrar's** or **DNS provider's**
  control panel (wherever you bought/manage the domain — Namecheap, Cloudflare, Google Domains,
  your registrar's own dashboard, etc.), not on the box itself. Look for a page called "DNS",
  "DNS Management", or "DNS Records", then add a record with:
  - **Type**: `A`
  - **Host/Name**: `@` for the bare domain, or a subdomain like `forum` (for `forum.example.com`)
  - **Value/Points to**: the box's public IP address
  - **TTL**: the default is fine

  DNS changes aren't instant — they can take anywhere from a couple of minutes to an hour to
  "propagate" (become visible everywhere). If `https://your-domain` doesn't load right after you
  set the record, that's usually just propagation delay — wait a bit and try again.
- **Reverse proxy / TLS ("HTTPS")** — Caddy, bundled in the compose stack, sits in front of the
  `web` container and is the only thing exposed to the internet (ports 80 and 443). It
  automatically requests a free TLS certificate from Let's Encrypt so the site loads over
  `https://` with a padlock, no certificate wrangling on your part. For that automatic
  certificate request to succeed, two things must already be true *before* you first run
  `docker compose up`: your DNS `A` record must already point at the box, and ports 80/443 must
  be reachable from the internet (see the firewall note in Option B below). If either isn't
  ready yet, Caddy's certificate request fails silently and the site won't load over HTTPS until
  you fix the DNS/firewall and restart it.
- **Firewall / security group** — a filter, either on the box itself (Ubuntu's `ufw`) or in your
  cloud provider's dashboard (often called a "firewall" or "security group"), controlling which
  ports the outside world can reach. This stack needs three open: **22** (SSH, so you can keep
  managing the box), **80** and **443** (so Caddy can serve the site and renew its certificate).
  Everything else — Postgres included — should stay closed; the compose file already keeps
  Postgres off the internet entirely (see the gotchas below).
- **Docker Compose commands you'll use throughout** — a quick reference:
  - `docker compose up -d` — start the stack in the background
  - `docker compose logs -f <service>` — tail a service's logs (e.g. `caddy`, `web`,
    `sync-worker`) — the first place to look if something isn't working
  - `docker compose restart <service>` — restart a single service without touching the rest
  - `docker compose down` — stop the stack (data in the `postgres` volume is preserved)

## Running at a subpath

Both options above assume Threadbare owns the whole domain (`https://forum.example.com/`). If
you'd rather share one domain across several things — say `https://www.example.com/` is already
your main site and you want the mirror at `https://www.example.com/discord-mirror` — Threadbare
supports that too, with one small edit to the `Caddyfile` (already bind-mounted onto the box in
`docker-compose.yml`, so this is a file edit, not a rebuild). This applies the same way whether
you got here via Option A or Option B — it's Caddy-side routing, unrelated to how the domain
itself is reached.

Replace the `Caddyfile`'s `reverse_proxy` block with:

```
{$THREADBARE_DOMAIN} {
	handle_path /discord-mirror/* {
		reverse_proxy web:5000 {
			header_up X-Forwarded-Prefix /discord-mirror
		}
	}
}
```

(Swap `/discord-mirror` for whatever path you want, but keep it identical in all three places it
appears above.) `handle_path` strips that prefix before the request reaches the `web` container,
so Threadbare's own routes stay simple and unprefixed internally — the `X-Forwarded-Prefix` header
is what tells Threadbare to add the prefix back into every link it renders, so pages, pagination,
and stylesheets all come back as `/discord-mirror/...` in the browser.

A couple of things to get right:

- `THREADBARE_DOMAIN`/DNS setup is exactly as described above — still just the bare domain. The
  path is handled entirely inside the `Caddyfile`, not DNS.
- `DISCORD_OAUTH_REDIRECT_URI` in `.env`, and the redirect URI registered in the Discord developer
  portal, must both include the full path (e.g.
  `https://www.example.com/discord-mirror/oauth/callback`) — same byte-for-byte requirement called
  out in the domain-change gotcha below, just with a path included this time.
- `docker compose restart caddy` after editing the file for the change to take effect.

If you don't need this, skip it entirely — the `Caddyfile` and `docker-compose.yml` this repo
ships work unmodified for a normal domain-root deployment.

## Option B — VPS (recommended)

A $5–10/month instance (Hetzner, DigitalOcean, Vultr, etc.) is comfortable at 2GB RAM.

1. Create an account with a VPS provider and provision a box running **Ubuntu LTS** (24.04 or
   22.04). During creation, upload your SSH key if the provider offers it — this saves you from
   a password login later. Note the box's **public IPv4 address**, shown in the provider's
   dashboard once the box is up.

   Once it's up, connect to it: `ssh root@<the box's public IP>` (or whichever username the
   provider set up). Then [install Docker + the Compose
   plugin](https://docs.docker.com/engine/install/) by following that page's instructions for
   Ubuntu.

   **Firewall**: most VPS providers open all ports by default, or offer a "firewall"/"security
   group" panel in their dashboard to lock this down. Either there, or via Ubuntu's own `ufw`
   (`ufw allow 22`, `ufw allow 80`, `ufw allow 443`, then `ufw enable`), make sure **22, 80, and
   443** are reachable and nothing else needs to be.

2. Clone this repo onto the box, then `cp .env.example .env` and fill in **at minimum**
   `POSTGRES_PASSWORD` (any strong random value) and `THREADBARE_DOMAIN` (the domain you're
   pointing at this box). Everything Discord-specific is collected by the setup wizard once
   the stack is up — don't fill those in by hand.
3. Point a DNS `A` record for that domain at the box's public IP — see **Before you start**
   above for exactly where and how. Do this before the next step, since Caddy needs it in place
   to get its TLS certificate.
4. `docker compose up -d`.
5. Visit `https://<your-domain>` — the setup wizard takes it from there (bot creation,
   preflight checks, the bot invite link, channel selection, then the OAuth login gate). When
   it finishes, the `web` container restarts itself automatically within a few seconds (the
   wizard hands off by exiting the process; Compose's `restart: unless-stopped` policy brings
   it back up already configured, now served by gunicorn instead of the wizard's dev server) —
   the finish page redirects itself once that's done. You still need to run
   `docker compose restart sync-worker` once by hand — it only reads configuration at its own
   startup and has no way to notice the change on its own.

Gotchas worth knowing before you go further:

- Set up unattended security upgrades on the box — it's the internet-facing part of this
  stack that isn't Threadbare's own code. On Ubuntu, this is one package:
  `apt install unattended-upgrades`, then `dpkg-reconfigure --priority=low
  unattended-upgrades` and answer "Yes" — this applies security patches automatically without
  you having to remember to `apt upgrade` regularly.
- Postgres has no published port in `docker-compose.yml` on purpose (internal Docker network
  only) — don't "fix" this by exposing it.
- There's no automated backup job yet (see [`ROADMAP.md`](../ROADMAP.md) §8) — mirrored message
  content is never backed up by design (Discord is the source of truth), but the small
  Threadbare-native config isn't backed up automatically either yet. An occasional VPS
  snapshot (most providers offer one-click snapshots in their dashboard) is a reasonable
  stopgap in the meantime.
- If the domain ever changes, update `THREADBARE_DOMAIN` in `.env` **and** the OAuth redirect
  URI registered in the Discord developer portal — they have to match exactly.

### Troubleshooting

- **Site doesn't load over HTTPS / certificate errors**: almost always means Caddy's Let's
  Encrypt request failed. Check `docker compose logs -f caddy`. The two usual causes are DNS not
  having propagated yet (see **Before you start**) or ports 80/443 not actually being reachable
  (check your firewall/security group). Once you fix the underlying cause, `docker compose
  restart caddy` to make it try again.
- **Discord login fails with an opaque error after you approve the app**: almost always an OAuth
  redirect URI mismatch — the URI registered in the Discord developer portal must match
  `THREADBARE_DOMAIN` byte-for-byte (scheme, host, path). Re-check both against what the wizard
  displayed.
- **A container won't start or keeps restarting**: `docker compose logs -f <service>` (try
  `web`, `sync-worker`, `migrate`, or `postgres`) will show the actual error — usually a missing
  or malformed value in `.env`.

## Option A — Self-host on your own hardware

The cheapest option: run `docker compose up` on any machine that stays on — a desktop, a home
server, or a Raspberry Pi–class box (the whole stack idles under 1GB RAM). Same
`docker-compose.yml` as Option B; the only real difference is *reachability*.

1. Install Docker + the Compose plugin on the machine, clone the repo, `cp .env.example .env`
   and fill in `POSTGRES_PASSWORD` (`THREADBARE_DOMAIN` only matters once you've picked a
   reachability option below).
2. Pick how the outside world reaches the box:
   - **Tailscale** — the simplest option for a handful of trusted users (e.g. just the mod
     team), with no public exposure at all. Install the client on the box and on every device
     that should have access from [tailscale.com/download](https://tailscale.com/download), run
     `tailscale up` on each and log in with the same account, then use the Tailscale IP or
     MagicDNS hostname it assigns the box as `THREADBARE_DOMAIN`.
   - **Cloudflare Tunnel** — gives you a real public hostname with TLS and no port forwarding,
     the better choice if the whole community should be able to reach it from anywhere. Add the
     domain to a (free) Cloudflare account, install `cloudflared` on the box, run `cloudflared
     tunnel login` then create and route a tunnel per [Cloudflare's
     docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/), and
     run `cloudflared tunnel run` alongside the compose stack (as a separate process, or its own
     `systemd` service so it survives reboots). Point `THREADBARE_DOMAIN` at the hostname
     Cloudflare gives you.
   - Classic port-forwarding + dynamic DNS works too, but isn't recommended: residential IPs
     churn, and every time yours changes you have to update both `THREADBARE_DOMAIN` and the
     OAuth redirect URI registered in the Discord developer portal, or logins start failing
     with an opaque error.
3. `docker compose up -d`, then visit whichever hostname you set up — the setup wizard takes
   it from there exactly as in Option B.

A couple of things worth knowing:

- If you're only ever accessing this from the same machine, `localhost` as the domain works
  fine and needs none of the above — just skip straight to `docker compose up -d`.
- The sync worker needs no inbound ports at all, ever — only the web app does, so Tailscale/
  Cloudflare Tunnel only need to reach the `web` (really, `caddy`) container.
- Some residential ISP terms technically prohibit "running a server." In practice this never
  comes up at hobby scale, but it's worth knowing before you tell people about it.
