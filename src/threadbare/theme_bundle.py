"""Pure validation of an uploaded custom-theme .zip bundle -- no Flask, no
DB, no filesystem writes, so it's unit-testable against in-memory zips. The
web layer (web/views/admin.py) calls validate_bundle before ever touching
disk; web/theme_storage.py re-checks member safety on extraction as defense
in depth.

Bundle shape (ROADMAP.md Phase 3 custom themes):
    theme.css      required -- must define the :root custom-property contract
    theme.json     optional manifest ({"display_name": ...})
    assets/...     optional media (images, fonts, audio, video)

theme.css references media by relative path (url(assets/x.png)); those are
served under /themes/custom/<slug>/ so the relative path resolves with no
rewriting. Validation catches broken/case-mismatched asset paths, the media
allowlist (no SVG/JS/HTML in v1 -- same-origin XSS surface), zip-slip
members, and zip bombs before anything is written to the themes volume.
"""

import io
import json
import re
import zipfile
from dataclasses import dataclass, field

# The :root custom-property contract every theme must define, mirroring
# web/static/theme-plain.css (the reference theme). This is a *minimum*, not an
# exact set: a theme may declare more (see OPTIONAL_CUSTOM_PROPERTIES below),
# and a unit test asserts theme-plain.css declares at least all of these,
# guarding against a property being dropped from the reference theme.
REQUIRED_CUSTOM_PROPERTIES = frozenset(
    {
        "--color-bg",
        "--color-bg-alt",
        "--color-fg",
        "--color-fg-muted",
        "--color-border",
        "--color-link",
        "--color-link-visited",
        "--color-accent",
        "--color-accent-fg",
        "--color-danger",
        "--font-body",
        "--font-mono",
        "--font-size-base",
        "--font-size-small",
        "--line-height-base",
        "--space-xs",
        "--space-sm",
        "--space-md",
        "--space-lg",
        "--radius",
        "--border-width",
        "--container-max-width",
    }
)

# Properties a theme MAY declare but isn't required to, documented here because
# a bundle author reading REQUIRED_CUSTOM_PROPERTIES alone had no way to learn
# they exist at all. Every built-in theme references these; each has a var()
# fallback at its use site, so omitting one degrades rather than breaks.
#
#   --embed-color   Set inline per-embed by rendering/embeds.py from Discord's
#                   own embed colour, so a theme never declares a value -- it
#                   only ever supplies the fallback for embeds with no colour.
#   --color-bg-visited
#                   Background for an already-visited board/topic row. Falls
#                   back to --color-bg-alt, which is also the zebra-stripe
#                   colour, so a theme that skips it loses the visited/unvisited
#                   distinction on striped rows.
OPTIONAL_CUSTOM_PROPERTIES = frozenset({"--embed-color", "--color-bg-visited"})

# Extension -> Content-Type allowlist for bundled assets. Deliberately
# excludes svg/html/js: assets are served from the app's own origin, and
# those can execute script on direct navigation (same-origin XSS). Revisit
# with sandboxing later. theme.css/theme.json are handled specially.
ASSET_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
}

# The two non-asset files allowed at the bundle root.
_CSS_NAME = "theme.css"
_MANIFEST_NAME = "theme.json"
_ALLOWED_ROOT_FILES = {_CSS_NAME, _MANIFEST_NAME}

# Default caps (overridable per call so tests can exercise rejection cheaply).
MAX_BUNDLE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 1000

# Shared with web/theme_storage.py, which re-checks every member at extraction
# time. Only these two constants are shared: the *checks* are deliberately
# duplicated (defense in depth -- see theme_storage.py's docstring), and two
# independent implementations is the point.
S_IFLNK = 0o120000
DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

_URL_RE = re.compile(r"url\(\s*(['\"]?)(?P<target>[^'\")]+)\1\s*\)", re.IGNORECASE)
_IMPORT_RE = re.compile(r"@import\s+(?:url\(\s*)?['\"](?P<target>[^'\"]+)['\"]", re.IGNORECASE)
# Truly-external references get a portability warning; data: URIs are
# self-contained so they're skipped entirely (neither error nor warning).
_EXTERNAL_PREFIXES = ("http:", "https:", "//")
_SELF_CONTAINED_PREFIXES = ("data:",)


def slugify(name: str) -> str:
    """A URL-safe theme slug from a display name: lowercase, runs of
    non-alphanumerics collapsed to single hyphens, trimmed. Empty if the name
    has no usable characters (the caller treats that as "name required").
    """
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@dataclass(frozen=True)
class BundleReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    display_name: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def _extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def _is_unsafe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    if name.startswith("/") or name.startswith("\\"):
        return True
    if DRIVE_LETTER_RE.match(name):  # drive-letter absolute (windows zip)
        return True
    parts = name.replace("\\", "/").split("/")
    if ".." in parts:
        return True
    mode = (info.external_attr >> 16) & 0o170000
    return mode == S_IFLNK


def _referenced_targets(css: str) -> list[str]:
    targets = []
    for match in _URL_RE.finditer(css):
        targets.append(match.group("target").strip())
    for match in _IMPORT_RE.finditer(css):
        targets.append(match.group("target").strip())
    return targets


def validate_bundle(
    zip_bytes: bytes,
    *,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES,
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_entries: int = MAX_ENTRIES,
) -> BundleReport:
    errors: list[str] = []
    warnings: list[str] = []
    display_name: str | None = None

    if len(zip_bytes) > max_bundle_bytes:
        return BundleReport(
            errors=[f"Bundle is too large ({len(zip_bytes)} bytes; limit {max_bundle_bytes})."]
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return BundleReport(errors=["File is not a valid .zip archive."])

    with zf:
        infos = zf.infolist()

        if len(infos) > max_entries:
            errors.append(f"Bundle has too many entries ({len(infos)}; limit {max_entries}).")

        total_uncompressed = 0
        member_names: set[str] = set()
        for info in infos:
            if _is_unsafe_member(info):
                errors.append(f"Unsafe path in bundle: {info.filename!r}.")
                continue
            if info.is_dir():
                continue
            member_names.add(info.filename)
            total_uncompressed += info.file_size
            if info.file_size > max_file_bytes:
                errors.append(
                    f"File too large: {info.filename!r} "
                    f"({info.file_size} bytes; limit {max_file_bytes})."
                )

        if total_uncompressed > max_uncompressed_bytes:
            errors.append(
                f"Bundle contents exceed the uncompressed size limit "
                f"({total_uncompressed} bytes; limit {max_uncompressed_bytes})."
            )

        # Extension allowlist for every file that isn't a root manifest/css.
        for name in member_names:
            if name in _ALLOWED_ROOT_FILES:
                continue
            ext = _extension(name)
            if ext not in ASSET_CONTENT_TYPES:
                errors.append(
                    f"Disallowed file type: {name!r} ({ext or 'no extension'}). "
                    f"Allowed media: {', '.join(sorted(ASSET_CONTENT_TYPES))}."
                )

        if _CSS_NAME not in member_names:
            errors.append("Bundle is missing a top-level theme.css.")
            return BundleReport(errors=errors, warnings=warnings)

        css = zf.read(_CSS_NAME).decode("utf-8", errors="replace")

        missing = sorted(p for p in REQUIRED_CUSTOM_PROPERTIES if not _declares(css, p))
        if missing:
            errors.append("theme.css is missing required custom properties: " + ", ".join(missing))

        for target in _referenced_targets(css):
            low = target.lower()
            if low.startswith(_SELF_CONTAINED_PREFIXES):
                continue
            if low.startswith(_EXTERNAL_PREFIXES):
                warnings.append(
                    f"theme.css references an external resource: {target} "
                    "(bundles are more portable when self-contained)."
                )
                continue
            path = target.split("?", 1)[0].split("#", 1)[0].lstrip("./")
            if not path:
                continue
            if path not in member_names:
                errors.append(f"theme.css references a missing asset: {target}")

        if _MANIFEST_NAME in member_names:
            try:
                manifest = json.loads(zf.read(_MANIFEST_NAME).decode("utf-8"))
                name = manifest.get("display_name") if isinstance(manifest, dict) else None
                if isinstance(name, str) and name.strip():
                    display_name = name.strip()
            except (json.JSONDecodeError, UnicodeDecodeError):
                warnings.append("theme.json is not valid JSON; ignoring it.")

    return BundleReport(errors=errors, warnings=warnings, display_name=display_name)


def _declares(css: str, prop: str) -> bool:
    return re.search(re.escape(prop) + r"\s*:", css) is not None
