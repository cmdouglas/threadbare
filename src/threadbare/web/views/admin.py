"""Mod admin page (ROADMAP.md §6): per-channel indexing toggle + read-only
sync health. Every route here is @mod_required -- Manage Server or
Administrator on the mirrored guild, computed at login (web/views/auth.py).

Deliberately does NOT include a "trigger re-backfill" control: the web app
and sync worker are separate processes with no IPC today, and building that
plumbing (LISTEN/NOTIFY, an internal RPC, or a polling flag) is out of
scope for this pass -- see ROADMAP.md §6.
"""

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

import threadbare
from threadbare import theme_bundle
from threadbare.db import admin_queries, queries
from threadbare.web import theme_storage, themes
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
        auto_index_new_channels = await admin_queries.get_auto_index_new_channels(conn)

    return render_template(
        "admin.html",
        channels=channels,
        heartbeat=heartbeat,
        heartbeat_stale=admin_queries.is_heartbeat_stale(heartbeat),
        app_version=threadbare.__version__,
        schema_version=schema_version,
        auto_index_new_channels=auto_index_new_channels,
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


async def _render_themes_page(*, errors=None, warnings=None, status=200):
    pool = current_app.config["POOL"]
    async with pool.connection() as conn:
        custom_themes = await queries.get_custom_themes(conn)
    return (
        render_template(
            "admin_themes.html",
            custom_themes=custom_themes,
            builtin_themes=list(themes.AVAILABLE_THEMES),
            errors=errors or [],
            warnings=warnings or [],
        ),
        status,
    )


@bp.route("/themes")
@mod_required
async def themes_index():
    return await _render_themes_page()


@bp.route("/themes", methods=["POST"])
@mod_required
async def register_theme():
    """Register (or replace) a custom theme from an uploaded .zip -- the
    first multipart/request.files handler in the codebase. Validated fully
    before anything touches disk; on failure the page re-renders with the
    specific problems named rather than a bare error.
    """
    pool = current_app.config["POOL"]
    settings = current_app.config["SETTINGS"]

    upload = request.files.get("bundle")
    name_field = (request.form.get("display_name") or "").strip()
    zip_bytes = upload.read() if upload is not None else b""

    errors: list[str] = []
    warnings: list[str] = []
    report = None
    if not zip_bytes:
        errors.append("No theme bundle was uploaded.")
    else:
        report = theme_bundle.validate_bundle(zip_bytes)
        errors.extend(report.errors)
        warnings.extend(report.warnings)

    filename_stem = ""
    if upload is not None and upload.filename:
        filename_stem = upload.filename.rsplit(".", 1)[0]
    display_name = name_field or (report.display_name if report else None) or filename_stem
    slug = theme_bundle.slugify(display_name or "")
    if not slug:
        errors.append("A theme name is required (from the form, theme.json, or the filename).")
    elif slug in themes.AVAILABLE_THEMES:
        errors.append(f"{slug!r} is a built-in theme name; choose a different name.")

    if errors:
        return await _render_themes_page(errors=errors, warnings=warnings, status=400)

    theme_storage.install(settings.theme_storage_dir, slug, zip_bytes)
    async with pool.connection() as conn:
        await admin_queries.insert_custom_theme(conn, slug=slug, display_name=display_name)
    return redirect(url_for("admin.themes_index"))


@bp.route("/themes/<slug>/delete", methods=["POST"])
@mod_required
async def delete_theme(slug: str):
    pool = current_app.config["POOL"]
    settings = current_app.config["SETTINGS"]
    async with pool.connection() as conn:
        await admin_queries.delete_custom_theme(conn, slug)
    theme_storage.remove(settings.theme_storage_dir, slug)
    return redirect(url_for("admin.themes_index"))


@bp.route("/themes/<slug>/download")
@mod_required
async def download_theme(slug: str):
    """Download any theme for reuse: a built-in as its raw .css (a starting
    template), a custom theme re-zipped from its extracted bundle on disk.
    """
    settings = current_app.config["SETTINGS"]
    if slug in themes.AVAILABLE_THEMES:
        return send_from_directory(
            current_app.static_folder, themes.AVAILABLE_THEMES[slug], as_attachment=True
        )
    if not theme_storage.theme_css_exists(settings.theme_storage_dir, slug):
        abort(404)
    data = theme_storage.rezip(settings.theme_storage_dir, slug)
    return Response(
        data,
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )
