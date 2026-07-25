import io
import os
import zipfile

from threadbare.db import admin_queries
from threadbare.theme_bundle import REQUIRED_CUSTOM_PROPERTIES
from threadbare.web import theme_storage

from .conftest import run


def _make_mod(client):
    with client.session_transaction() as sess:
        sess["is_mod"] = True


def _theme_dir(client) -> str:
    return client.application.config["SETTINGS"].theme_storage_dir


def _valid_css() -> bytes:
    body = "\n".join(f"  {p}: initial;" for p in sorted(REQUIRED_CUSTOM_PROPERTIES))
    return (":root {\n" + body + "\n}\n").encode()


def _bundle(css: bytes | None = None, assets: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("theme.css", css if css is not None else _valid_css())
        for name, content in (assets or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def _upload(client, zip_bytes, *, display_name="My Theme", filename="theme.zip"):
    return client.post(
        "/admin/themes",
        data={"display_name": display_name, "bundle": (io.BytesIO(zip_bytes), filename)},
        content_type="multipart/form-data",
    )


def test_themes_page_requires_mod(client):
    resp = client.get("/admin/themes")

    assert resp.status_code == 403


def test_register_a_valid_bundle_installs_and_records_it(client, web_conn):
    _make_mod(client)

    resp = _upload(client, _bundle(assets={"assets/bg.png": b"\x89PNG"}), display_name="Neon")

    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/themes"
    assert run(admin_queries.get_custom_theme(web_conn, "neon"))["display_name"] == "Neon"
    assert theme_storage.theme_css_exists(_theme_dir(client), "neon")
    assert os.path.exists(os.path.join(_theme_dir(client), "neon", "assets", "bg.png"))


def test_register_invalid_bundle_names_the_errors_and_installs_nothing(client, web_conn):
    _make_mod(client)

    resp = _upload(client, _bundle(css=b":root{ --color-bg: #fff; }"), display_name="Broken")

    assert resp.status_code == 400
    assert b"--color-fg" in resp.data  # a named missing property
    assert run(admin_queries.get_custom_theme(web_conn, "broken")) is None
    assert not theme_storage.theme_css_exists(_theme_dir(client), "broken")


def test_register_rejects_a_builtin_name_collision(client, web_conn):
    _make_mod(client)

    resp = _upload(client, _bundle(), display_name="Plain")

    assert resp.status_code == 400
    assert run(admin_queries.get_custom_theme(web_conn, "plain")) is None


def test_registered_theme_is_listed_on_the_page(client, web_conn):
    _make_mod(client)
    theme_storage.install(_theme_dir(client), "neon", _bundle())
    run(admin_queries.insert_custom_theme(web_conn, slug="neon", display_name="Neon Dreams"))

    resp = client.get("/admin/themes")

    assert b"Neon Dreams" in resp.data
    assert b"neon" in resp.data


def test_delete_removes_the_row_and_the_files(client, web_conn):
    _make_mod(client)
    theme_storage.install(_theme_dir(client), "neon", _bundle())
    run(admin_queries.insert_custom_theme(web_conn, slug="neon", display_name="Neon"))

    resp = client.post("/admin/themes/neon/delete")

    assert resp.status_code == 302
    assert run(admin_queries.get_custom_theme(web_conn, "neon")) is None
    assert not os.path.exists(os.path.join(_theme_dir(client), "neon"))


def test_download_a_custom_theme_returns_a_zip(client, web_conn):
    _make_mod(client)
    theme_storage.install(_theme_dir(client), "neon", _bundle(assets={"assets/bg.png": b"PNG"}))
    run(admin_queries.insert_custom_theme(web_conn, slug="neon", display_name="Neon"))

    resp = client.get("/admin/themes/neon/download")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    assert 'filename="neon.zip"' in resp.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        assert "theme.css" in zf.namelist()
        assert "assets/bg.png" in zf.namelist()


def test_download_a_builtin_theme_returns_its_css(client):
    _make_mod(client)

    resp = client.get("/admin/themes/plain/download")

    assert resp.status_code == 200
    assert b"--color-bg" in resp.data
    assert "attachment" in resp.headers["Content-Disposition"]


def test_admin_index_links_to_the_themes_page(client):
    _make_mod(client)

    resp = client.get("/admin/")

    assert b"/admin/themes" in resp.data
