import io
import zipfile
from pathlib import Path

import pytest

from threadbare.web import theme_storage


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_install_extracts_the_bundle(tmp_path):
    data = _make_zip({"theme.css": b":root{}", "assets/bg.png": b"\x89PNG"})

    theme_storage.install(str(tmp_path), "my-theme", data)

    assert (tmp_path / "my-theme" / "theme.css").read_bytes() == b":root{}"
    assert (tmp_path / "my-theme" / "assets" / "bg.png").read_bytes() == b"\x89PNG"


def test_theme_css_exists(tmp_path):
    assert theme_storage.theme_css_exists(str(tmp_path), "my-theme") is False

    theme_storage.install(str(tmp_path), "my-theme", _make_zip({"theme.css": b"x"}))

    assert theme_storage.theme_css_exists(str(tmp_path), "my-theme") is True


def test_install_replaces_an_existing_theme(tmp_path):
    theme_storage.install(
        str(tmp_path), "my-theme", _make_zip({"theme.css": b"old", "assets/a.png": b"a"})
    )

    theme_storage.install(str(tmp_path), "my-theme", _make_zip({"theme.css": b"new"}))

    assert (tmp_path / "my-theme" / "theme.css").read_bytes() == b"new"
    # the stale asset from the first install must be gone after replacement
    assert not (tmp_path / "my-theme" / "assets" / "a.png").exists()


def test_remove_deletes_the_theme_dir(tmp_path):
    theme_storage.install(str(tmp_path), "my-theme", _make_zip({"theme.css": b"x"}))

    theme_storage.remove(str(tmp_path), "my-theme")

    assert not (tmp_path / "my-theme").exists()


def test_remove_is_a_noop_when_absent(tmp_path):
    theme_storage.remove(str(tmp_path), "never-installed")  # must not raise


def test_rezip_round_trips(tmp_path):
    theme_storage.install(
        str(tmp_path), "my-theme", _make_zip({"theme.css": b":root{}", "assets/bg.png": b"PNGDATA"})
    )

    data = theme_storage.rezip(str(tmp_path), "my-theme")

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert "theme.css" in names
        assert "assets/bg.png" in names
        assert zf.read("assets/bg.png") == b"PNGDATA"


def test_install_rejects_zip_slip_member(tmp_path):
    data = _make_zip({"theme.css": b"x", "../escape.png": b"evil"})

    with pytest.raises(ValueError):
        theme_storage.install(str(tmp_path), "my-theme", data)

    # nothing partially installed
    assert not (tmp_path / "my-theme").exists()
    assert not (Path(tmp_path).parent / "escape.png").exists()
