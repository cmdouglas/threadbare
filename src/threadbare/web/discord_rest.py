"""Minimal, gateway-free Discord REST access for the web app -- deliberately
not discord.py (that stays a sync-worker-only dependency; this is a single
REST call, not a bot session) and not discord.py's transitive aiohttp,
using httpx instead to keep web/ decoupled from the sync worker's stack.
"""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx

DISCORD_API_BASE = "https://discord.com/api/v10"


class SignedUrlExpiryError(Exception):
    pass


class AttachmentRefreshError(Exception):
    pass


def parse_expiry_from_signed_url(url: str) -> datetime:
    """Discord's signed CDN URLs (attachments, refreshed or original) encode
    their own expiry as an `ex=` hex Unix-timestamp query parameter -- no
    separate expiry field exists anywhere in the API response. Note the
    sync worker's own ingest-time write (backfill.py's
    _estimate_attachment_url_expiry) does NOT parse this; it just estimates
    now()+24h, since backfill has no freshly-refreshed URL to parse yet.
    This function is what lets the attachment proxy record the *exact*
    expiry once it does have one.
    """
    query = parse_qs(urlparse(url).query)
    ex_values = query.get("ex")
    if not ex_values:
        raise SignedUrlExpiryError(f"no ex= expiry parameter in {url!r}")
    try:
        timestamp = int(ex_values[0], 16)
    except ValueError as e:
        raise SignedUrlExpiryError(f"malformed ex= parameter in {url!r}") from e
    return datetime.fromtimestamp(timestamp, tz=UTC)


async def refresh_attachment_urls(
    bot_token: str,
    urls_to_refresh: list[str],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, str]:
    """Calls Discord's bulk attachment-URL-refresh endpoint, returning a
    mapping of original URL -> refreshed URL.

    UNVERIFIED CONTRACT, flagged deliberately rather than silently: this
    endpoint's request/response shape isn't authoritatively documented
    anywhere we could confirm (checked docs.discord.com, the discord-api-docs
    repo, and installed discord.py, which doesn't implement it at all).
    Implemented against the best cross-referenced understanding -- POST
    /attachments/refresh-urls, body {"attachment_urls": [...]}, response
    {"refreshed_urls": [{"original": ..., "refreshed": ...}, ...]} -- but
    NOT exercised against a real Discord API call with a real bot token.
    It's also not confirmed whether this endpoint even accepts bot-token
    auth (vs. user/client tokens only); if it doesn't, this 401s and the
    proxy route degrades to "attachment unavailable" rather than
    misbehaving silently, but the underlying assumption (bot-token-only
    access works here at all, DESIGN.md §3.1) needs a real smoke test
    before this is trusted in production. Tracked as a live-test gap in
    DESIGN.md §10 / ROADMAP.md §4, matching this project's convention for
    untested-in-practice code paths.

    Raises AttachmentRefreshError on any failure (network error, non-2xx
    response, or an unexpected response shape) so the caller (the
    /att/{id} proxy route) can degrade to a 404 rather than a 500 -- the
    most likely real-world cause is the message/attachment having since
    vanished upstream, or (per the above) a token-scope mismatch.
    """
    async with httpx.AsyncClient(transport=transport) as client:
        try:
            response = await client.post(
                f"{DISCORD_API_BASE}/attachments/refresh-urls",
                headers={"Authorization": f"Bot {bot_token}"},
                json={"attachment_urls": urls_to_refresh},
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise AttachmentRefreshError(str(e)) from e

        try:
            data = response.json()
            return {item["original"]: item["refreshed"] for item in data["refreshed_urls"]}
        except (KeyError, TypeError, ValueError) as e:
            raise AttachmentRefreshError(f"unexpected response shape: {e}") from e
