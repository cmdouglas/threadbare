"""Mod admin page (ROADMAP.md §6): per-channel indexing toggle + read-only
sync health. The custom-theme CRUD, which used to live here too, is its own
blueprint in admin_themes.py -- a different feature with its own template and
its own sibling module (web/views/themes.py serves the bundle files). Every
route here is @mod_required -- Manage Server or
Administrator on the mirrored guild, computed at login (web/views/auth.py).

Also carries the mod-triggered maintenance controls (Resync and Regroup).
Those were deliberately absent for a long time because the web app and sync
worker are separate processes with no IPC -- ROADMAP.md §6 records that.
sync_worker/jobs.py is now that plumbing: these routes only queue a row, and
the worker claims it on its own timer. Nothing here ever does the work
itself, which is what keeps an expensive history re-walk off a web request.
"""

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import threadbare
from threadbare.db import admin_queries
from threadbare.sync_worker import jobs
from threadbare.web.authz import mod_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

# Offered merge-gap values, in seconds. 420 (7 minutes) is Discord's own
# visual grouping window and the default; the rest bracket it for servers
# that chat faster or slower. A fixed list rather than a free number field --
# see set_merge_posts below.
GAP_CHOICES = (60, 180, 420, 900, 3600)


@bp.route("/")
@mod_required
async def index():
    settings = current_app.config["SETTINGS"]
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        channels = await admin_queries.get_channels_with_sync_state(conn, settings.discord_guild_id)
        heartbeat = await admin_queries.get_worker_heartbeat(conn)
        schema_version = await admin_queries.get_latest_migration_version(conn)
        auto_index_new_channels = await admin_queries.get_auto_index_new_channels(conn)
        merge_settings = await admin_queries.get_merge_settings(conn)
        channels_needing_regroup = await admin_queries.count_channels_needing_regroup(conn)
        recent_jobs = await jobs.recent(conn)
        pending_jobs = await jobs.pending_targets(conn)

    return render_template(
        "admin.html",
        channels=channels,
        heartbeat=heartbeat,
        heartbeat_stale=admin_queries.is_heartbeat_stale(heartbeat),
        app_version=threadbare.__version__,
        schema_version=schema_version,
        auto_index_new_channels=auto_index_new_channels,
        merge_settings=merge_settings,
        channels_needing_regroup=channels_needing_regroup,
        recent_jobs=recent_jobs,
        pending_jobs=pending_jobs,
        gap_choices=GAP_CHOICES,
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


@bp.route("/channels/<int:channel_id>/toggle-visibility-enrolled", methods=["POST"])
@mod_required
async def toggle_visibility_enrolled(channel_id: int):
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        current = await admin_queries.get_channel_visibility_enrolled(conn, channel_id)
        if current is None:
            abort(404)
        await admin_queries.set_channel_visibility_enrolled(conn, channel_id, not current)

    return redirect(url_for("admin.index"))


@bp.route("/settings/toggle-auto-index", methods=["POST"])
@mod_required
async def toggle_auto_index():
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        current = await admin_queries.get_auto_index_new_channels(conn)
        await admin_queries.set_auto_index_new_channels(conn, not current)

    return redirect(url_for("admin.index"))


@bp.route("/settings/merge-posts", methods=["POST"])
@mod_required
async def set_merge_posts():
    """The merge toggle and its gap threshold, saved together -- both
    invalidate every stored starts_group, so they share one write and one
    generation bump rather than two.
    """
    enabled = request.form.get("enabled") == "on"
    gap_seconds = request.form.get("gap_seconds", type=int)
    if gap_seconds not in GAP_CHOICES:
        # A fixed choice list rather than a free number field: the gap is a
        # taste setting with a handful of sane values, and an arbitrary one
        # (0, or a week) produces confusing grouping rather than an error.
        abort(400)

    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        await admin_queries.set_merge_settings(conn, enabled=enabled, gap_seconds=gap_seconds)

    return redirect(url_for("admin.index"))


@bp.route("/jobs", methods=["POST"])
@mod_required
async def enqueue_job():
    """Queue a resync or regroup for the sync worker to pick up.

    POST-only and never GET: both are expensive, non-idempotent side effects,
    and a resync in particular is a full history re-walk that a prefetching
    browser must not be able to trigger by following a link.
    """
    kind = request.form.get("kind")
    if kind not in (jobs.REGROUP, jobs.RESYNC):
        abort(400)
    channel_id = request.form.get("channel_id", type=int)  # None = every channel

    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        # enqueue returns None when one is already pending -- a double submit,
        # or two mods clicking at once. The button is disabled in that state,
        # so this is the race rather than the normal path: land back on the
        # page showing the pending job rather than erroring at someone who
        # asked for exactly the right thing.
        await jobs.enqueue(
            conn, kind=kind, channel_id=channel_id, requested_by=session.get("user_id")
        )

    return redirect(url_for("admin.index"))
