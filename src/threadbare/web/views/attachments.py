from datetime import UTC, datetime, timedelta

from flask import Blueprint, current_app, redirect, render_template

from threadbare.db import queries
from threadbare.web.discord_rest import (
    AttachmentRefreshError,
    SignedUrlExpiryError,
    parse_expiry_from_signed_url,
    refresh_attachment_urls,
)

bp = Blueprint("attachments", __name__)

# The "expiry cache" ROADMAP.md §4 asks for: Postgres itself, via
# url_expires_at, refreshed on demand -- a safety margin avoids handing out
# a URL that expires moments after this redirect is issued.
REFRESH_MARGIN = timedelta(minutes=5)


@bp.route("/att/<int:attachment_id>")
async def attachment_proxy(attachment_id: int):
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        attachment = await queries.get_attachment_by_id(conn, attachment_id)
        if attachment is None:
            return render_template("attachment_unavailable.html"), 404

        if attachment["url_expires_at"] > datetime.now(UTC) + REFRESH_MARGIN:
            return redirect(attachment["cached_url"])

        settings = current_app.config["SETTINGS"]
        try:
            refreshed = await refresh_attachment_urls(
                settings.discord_bot_token, [attachment["cached_url"]]
            )
            new_url = refreshed[attachment["cached_url"]]
            new_expiry = parse_expiry_from_signed_url(new_url)
        except (AttachmentRefreshError, KeyError, SignedUrlExpiryError):
            # Most likely cause: the message/attachment has since vanished
            # upstream, or (see DESIGN.md §10) this endpoint's bot-token
            # support is itself unconfirmed and the call 401s.
            return render_template("attachment_unavailable.html"), 404

        await queries.update_attachment_cache(
            conn, attachment_id, cached_url=new_url, url_expires_at=new_expiry
        )
        return redirect(new_url)
