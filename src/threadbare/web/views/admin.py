"""Mod admin page (ROADMAP.md §6): per-channel indexing toggle + read-only
sync health. Every route here is @mod_required -- Manage Server or
Administrator on the mirrored guild, computed at login (web/views/auth.py).

Deliberately does NOT include a "trigger re-backfill" control: the web app
and sync worker are separate processes with no IPC today, and building that
plumbing (LISTEN/NOTIFY, an internal RPC, or a polling flag) is out of
scope for this pass -- see ROADMAP.md §6.
"""

from flask import Blueprint, abort, current_app, redirect, render_template, url_for

import threadbare
from threadbare.db import admin_queries
from threadbare.web.authz import mod_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@mod_required
async def index():
    settings = current_app.config["SETTINGS"]
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channels = await admin_queries.get_channels_with_sync_state(conn, settings.discord_guild_id)
        heartbeat = await admin_queries.get_worker_heartbeat(conn)
        schema_version = await admin_queries.get_latest_migration_version(conn)

    return render_template(
        "admin.html",
        channels=channels,
        heartbeat=heartbeat,
        heartbeat_stale=admin_queries.is_heartbeat_stale(heartbeat),
        app_version=threadbare.__version__,
        schema_version=schema_version,
    )


@bp.route("/channels/<int:channel_id>/toggle-indexed", methods=["POST"])
@mod_required
async def toggle_indexed(channel_id: int):
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        current = await admin_queries.get_channel_indexed(conn, channel_id)
        if current is None:
            abort(404)
        await admin_queries.set_channel_indexed(conn, channel_id, not current)

    return redirect(url_for("admin.index"))
