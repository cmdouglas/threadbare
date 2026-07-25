"""Public serving of custom-theme bundle files from the themes volume
(Settings.theme_storage_dir). One route serves both theme.css and every media
asset under a theme's directory, so a relative url(assets/x.png) in the CSS
resolves with no rewriting.

Login-exempt (see authz.LOGIN_EXEMPT_ENDPOINTS), mirroring `static`: a theme's
CSS is a subresource the login page itself may need, and themes are
non-sensitive cosmetic content. Every response is served with an explicit
Content-Type from the allowlist plus X-Content-Type-Options: nosniff, and only
allowlisted extensions are served at all -- defense in depth against MIME
sniffing / same-origin content injection (theme_bundle.validate_bundle already
blocks disallowed types at upload).
"""

import os

from flask import Blueprint, abort, current_app, send_from_directory
from werkzeug.security import safe_join

from threadbare.theme_bundle import ASSET_CONTENT_TYPES

bp = Blueprint("themes", __name__)


@bp.route("/themes/custom/<slug>/<path:asset_path>")
def custom_asset(slug: str, asset_path: str):
    root = current_app.config["SETTINGS"].theme_storage_dir
    theme_dir = safe_join(root, slug)
    if theme_dir is None:
        abort(404)

    if asset_path == "theme.css":
        content_type = "text/css"
    else:
        ext = os.path.splitext(asset_path)[1].lower()
        content_type = ASSET_CONTENT_TYPES.get(ext)
        if content_type is None:
            abort(404)

    # conditional=True gives ETag/Last-Modified revalidation and HTTP Range
    # support (audio/video seeking); send_from_directory safe-joins asset_path.
    response = send_from_directory(theme_dir, asset_path, mimetype=content_type, conditional=True)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
