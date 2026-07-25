"""Filesystem storage for custom-theme bundles, extracted onto the themes
volume (Settings.theme_storage_dir). The DB (custom_themes) holds only
metadata; the actual theme.css + media assets live here, one directory per
slug, served by web/views/themes.py.

Install is atomic: a bundle is extracted into a temp dir and then os.replace'd
into place, so a half-written theme is never served and re-uploading a slug
swaps cleanly. Extraction re-validates every member's path (defense in depth
with theme_bundle.validate_bundle, which the web layer already ran) to guard
against zip-slip -- never a blind extractall.
"""

import io
import os
import re
import shutil
import zipfile

_S_IFLNK = 0o120000


def _theme_dir(root: str, slug: str) -> str:
    return os.path.join(root, slug)


def _is_unsafe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    if name.startswith("/") or name.startswith("\\") or re.match(r"^[A-Za-z]:", name):
        return True
    if ".." in name.replace("\\", "/").split("/"):
        return True
    return (info.external_attr >> 16) & 0o170000 == _S_IFLNK


def _safe_extract(zip_bytes: bytes, dest: str) -> None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if _is_unsafe_member(info):
                raise ValueError(f"Unsafe path in bundle: {info.filename!r}")
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = os.path.join(dest, info.filename)
            # Final containment check against symlink/normalization surprises.
            if os.path.commonpath([os.path.realpath(dest), os.path.realpath(target)]) != (
                os.path.realpath(dest)
            ):
                raise ValueError(f"Unsafe path in bundle: {info.filename!r}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def install(root: str, slug: str, zip_bytes: bytes) -> None:
    """Extract a (already-validated) bundle into <root>/<slug>, atomically
    replacing any existing install. Raises ValueError on an unsafe member.
    """
    os.makedirs(root, exist_ok=True)
    staging = _theme_dir(root, f".{slug}.staging")
    old = _theme_dir(root, f".{slug}.old")
    final = _theme_dir(root, slug)

    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    try:
        _safe_extract(zip_bytes, staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(final):
        os.replace(final, old)
    os.replace(staging, final)
    shutil.rmtree(old, ignore_errors=True)


def remove(root: str, slug: str) -> None:
    """Delete a theme's directory. A no-op if it isn't present."""
    aside = _theme_dir(root, f".{slug}.removing")
    final = _theme_dir(root, slug)
    if not os.path.exists(final):
        return
    shutil.rmtree(aside, ignore_errors=True)
    os.replace(final, aside)
    shutil.rmtree(aside, ignore_errors=True)


def theme_css_exists(root: str, slug: str) -> bool:
    return os.path.isfile(os.path.join(_theme_dir(root, slug), "theme.css"))


def rezip(root: str, slug: str) -> bytes:
    """Re-zip a theme's directory for download -- the bundle is stored
    extracted, not as the original archive, so downloading it (to share or
    tweak) rebuilds a .zip from disk.
    """
    base = _theme_dir(root, slug)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                full = os.path.join(dirpath, name)
                arcname = os.path.relpath(full, base)
                zf.write(full, arcname)
    return buf.getvalue()
