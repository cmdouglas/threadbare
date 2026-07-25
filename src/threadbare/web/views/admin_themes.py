"""Mod-only custom-theme registration/deletion/download (ROADMAP.md Phase 3).

Split out of views/admin.py, which is the per-channel indexing + sync-health
page: these are separate features that happen to share a mod-only gate, and
having both in one module meant admin.py's docstring described only half of it.
Registered under the same /admin URL prefix, so the routes and the mod
expectation are unchanged.

Serving a registered theme's files is a different concern again and lives in
views/themes.py (public, login-exempt).
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

from threadbare import theme_bundle
from threadbare.db import admin_queries, queries
from threadbare.web import theme_storage, themes
from threadbare.web.authz import mod_required

bp = Blueprint("admin_themes", __name__, url_prefix="/admin")


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
    return redirect(url_for("admin_themes.themes_index"))


@bp.route("/themes/<slug>/delete", methods=["POST"])
@mod_required
async def delete_theme(slug: str):
    pool = current_app.config["POOL"]
    settings = current_app.config["SETTINGS"]
    async with pool.connection() as conn:
        await admin_queries.delete_custom_theme(conn, slug)
    theme_storage.remove(settings.theme_storage_dir, slug)
    return redirect(url_for("admin_themes.themes_index"))


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
