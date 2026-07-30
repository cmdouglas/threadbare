import io
import json
import re
import zipfile
from pathlib import Path

from threadbare import theme_bundle

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAIN_CSS = REPO_ROOT / "src" / "threadbare" / "web" / "static" / "theme-plain.css"
THEMING_GUIDE = REPO_ROOT / "docs" / "theming.md"


def _valid_css() -> str:
    body = "\n".join(f"  {p}: initial;" for p in sorted(theme_bundle.REQUIRED_CUSTOM_PROPERTIES))
    return ":root {\n" + body + "\n}\n"


def _make_zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_valid_bundle_passes_with_no_errors():
    data = _make_zip({"theme.css": _valid_css()})

    report = theme_bundle.validate_bundle(data)

    assert report.errors == []


def test_missing_theme_css_is_an_error():
    data = _make_zip({"assets/bg.png": b"\x89PNG"})

    report = theme_bundle.validate_bundle(data)

    assert any("theme.css" in e for e in report.errors)


def test_missing_custom_properties_are_named():
    css = ":root { --color-bg: #fff; }"  # only one of the 23
    data = _make_zip({"theme.css": css})

    report = theme_bundle.validate_bundle(data)

    assert any("--color-fg" in e for e in report.errors)
    assert any("--space-md" in e for e in report.errors)


def test_valid_relative_asset_reference_passes():
    css = _valid_css() + "\nbody { background: url(assets/bg.png); }"
    data = _make_zip({"theme.css": css, "assets/bg.png": b"\x89PNG"})

    report = theme_bundle.validate_bundle(data)

    assert report.errors == []


def test_broken_relative_asset_path_is_named():
    css = _valid_css() + "\nbody { background: url(assets/missing.png); }"
    data = _make_zip({"theme.css": css})

    report = theme_bundle.validate_bundle(data)

    assert any("assets/missing.png" in e for e in report.errors)


def test_case_mismatched_asset_path_is_named():
    css = _valid_css() + "\nbody { background: url(assets/BG.png); }"
    data = _make_zip({"theme.css": css, "assets/bg.png": b"\x89PNG"})

    report = theme_bundle.validate_bundle(data)

    assert any("assets/BG.png" in e for e in report.errors)


def test_external_url_is_a_warning_not_an_error():
    css = _valid_css() + "\nbody { background: url(https://cdn.example/bg.png); }"
    data = _make_zip({"theme.css": css})

    report = theme_bundle.validate_bundle(data)

    assert report.errors == []
    assert any("https://cdn.example/bg.png" in w for w in report.warnings)


def test_data_uri_reference_is_neither_error_nor_warning():
    css = _valid_css() + "\nbody { background: url(data:image/png;base64,iVBOR); }"
    data = _make_zip({"theme.css": css})

    report = theme_bundle.validate_bundle(data)

    assert report.errors == []
    assert report.warnings == []


def test_absolute_path_member_is_rejected():
    data = _make_zip({"theme.css": _valid_css(), "/etc/passwd": b"x"})

    report = theme_bundle.validate_bundle(data)

    assert report.errors != []


def test_parent_traversal_member_is_rejected():
    data = _make_zip({"theme.css": _valid_css(), "../escape.png": b"x"})

    report = theme_bundle.validate_bundle(data)

    assert report.errors != []


def test_symlink_member_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("theme.css", _valid_css())
        info = zipfile.ZipInfo("assets/evil")
        info.external_attr = 0o120777 << 16  # S_IFLNK
        zf.writestr(info, "/etc/passwd")

    report = theme_bundle.validate_bundle(buf.getvalue())

    assert report.errors != []


def test_disallowed_extension_is_rejected():
    data = _make_zip({"theme.css": _valid_css(), "assets/evil.js": b"alert(1)"})

    report = theme_bundle.validate_bundle(data)

    assert any(".js" in e or "evil.js" in e for e in report.errors)


def test_svg_is_rejected_in_v1():
    data = _make_zip({"theme.css": _valid_css(), "assets/pic.svg": b"<svg></svg>"})

    report = theme_bundle.validate_bundle(data)

    assert report.errors != []


def test_allowed_media_extensions_pass():
    files = {"theme.css": _valid_css()}
    for name in ("assets/a.png", "assets/b.woff2", "assets/c.mp3", "assets/d.mp4"):
        files[name] = b"x"
    data = _make_zip(files)

    report = theme_bundle.validate_bundle(data)

    assert report.errors == []


def test_theme_json_display_name_is_parsed():
    data = _make_zip(
        {"theme.css": _valid_css(), "theme.json": json.dumps({"display_name": "My Cool Theme"})}
    )

    report = theme_bundle.validate_bundle(data)

    assert report.display_name == "My Cool Theme"


def test_invalid_theme_json_is_a_warning_not_a_crash():
    data = _make_zip({"theme.css": _valid_css(), "theme.json": "{not valid json"})

    report = theme_bundle.validate_bundle(data)

    assert report.errors == []
    assert report.warnings != []


def test_not_a_zip_is_an_error():
    report = theme_bundle.validate_bundle(b"this is not a zip file")

    assert report.errors != []


def test_bundle_over_size_cap_is_rejected():
    data = _make_zip({"theme.css": _valid_css()})

    report = theme_bundle.validate_bundle(data, max_bundle_bytes=10)

    assert report.errors != []


def test_too_many_entries_is_rejected():
    files = {"theme.css": _valid_css()}
    for i in range(5):
        files[f"assets/a{i}.png"] = b"x"
    data = _make_zip(files)

    report = theme_bundle.validate_bundle(data, max_entries=2)

    assert report.errors != []


def test_uncompressed_inflation_cap_is_rejected():
    # highly compressible payload: small on disk, large inflated
    data = _make_zip({"theme.css": _valid_css(), "assets/big.png": b"\x00" * 100_000})

    report = theme_bundle.validate_bundle(data, max_uncompressed_bytes=1000)

    assert report.errors != []


def test_slugify_produces_url_safe_slugs():
    assert theme_bundle.slugify("Neon Dreams!!") == "neon-dreams"
    assert theme_bundle.slugify("  Retro  90s  ") == "retro-90s"
    assert theme_bundle.slugify("MySpace ☆ Vibes") == "myspace-vibes"


def test_slugify_is_empty_for_a_nameless_input():
    assert theme_bundle.slugify("???") == ""


def test_theme_plain_css_declares_every_required_custom_property():
    """The reference theme must satisfy the contract it defines. A subset check,
    not equality: the contract is a *minimum*, so a theme declaring extras (see
    theme_bundle.OPTIONAL_CUSTOM_PROPERTIES) is fine -- an equality assertion
    here just meant adding any new property broke this test.
    """
    css = PLAIN_CSS.read_text()
    root_block = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL).group(1)
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", root_block))

    missing = set(theme_bundle.REQUIRED_CUSTOM_PROPERTIES) - declared
    assert not missing, f"theme-plain.css is missing required properties: {sorted(missing)}"


def test_theme_plain_css_declares_nothing_undocumented():
    """Anything the reference theme declares beyond the required set must be
    listed in OPTIONAL_CUSTOM_PROPERTIES, so a bundle author can discover it.
    """
    css = PLAIN_CSS.read_text()
    root_block = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL).group(1)
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", root_block))

    undocumented = (
        declared
        - set(theme_bundle.REQUIRED_CUSTOM_PROPERTIES)
        - set(theme_bundle.OPTIONAL_CUSTOM_PROPERTIES)
    )
    assert not undocumented, (
        "theme-plain.css declares properties that are neither required nor "
        f"documented as optional: {sorted(undocumented)}"
    )


def _properties_named_in(markdown: str) -> set[str]:
    return set(re.findall(r"`(--[a-z0-9-]+)`", markdown))


def test_theming_guide_documents_every_required_custom_property():
    """docs/theming.md is what a third-party theme author reads instead of the
    source, so a property added to the contract and not written up there is
    invisible to exactly the audience it exists for. Same drift guard the
    reference theme gets above, pointed at the prose.
    """
    documented = _properties_named_in(THEMING_GUIDE.read_text())

    missing = set(theme_bundle.REQUIRED_CUSTOM_PROPERTIES) - documented
    assert not missing, f"docs/theming.md doesn't document required properties: {sorted(missing)}"


def test_theming_guide_invents_no_custom_properties():
    """The other direction: a property named in the guide that the app never
    reads is worse than an undocumented one -- an author styles it, sees no
    effect, and distrusts the rest of the document.
    """
    documented = _properties_named_in(THEMING_GUIDE.read_text())

    invented = (
        documented
        - set(theme_bundle.REQUIRED_CUSTOM_PROPERTIES)
        - set(theme_bundle.OPTIONAL_CUSTOM_PROPERTIES)
    )
    assert not invented, f"docs/theming.md names properties nothing reads: {sorted(invented)}"
