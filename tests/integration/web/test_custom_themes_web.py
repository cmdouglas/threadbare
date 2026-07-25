import io
import zipfile

from threadbare.db import admin_queries
from threadbare.web import theme_storage

from .conftest import run


def _theme_dir(client) -> str:
    return client.application.config["SETTINGS"].theme_storage_dir


def _bundle(css: bytes = b":root{}", assets: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("theme.css", css)
        for name, content in (assets or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def _register(
    client, web_conn, *, slug="my-theme", display_name="My Theme", assets=None, css=b":root{}"
):
    theme_storage.install(_theme_dir(client), slug, _bundle(css=css, assets=assets))
    run(admin_queries.insert_custom_theme(web_conn, slug=slug, display_name=display_name))


def test_serving_theme_css_returns_text_css_with_nosniff(client, web_conn):
    _register(client, web_conn, css=b":root{ --color-bg: #000; }")

    resp = client.get("/themes/custom/my-theme/theme.css")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/css")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert b"--color-bg" in resp.data


def test_serving_a_media_asset_uses_the_allowlist_content_type(client, web_conn):
    _register(client, web_conn, assets={"assets/bg.png": b"\x89PNG\r\n"})

    resp = client.get("/themes/custom/my-theme/assets/bg.png")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "image/png"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_serving_a_disallowed_extension_404s(client, web_conn):
    _register(client, web_conn)
    # even if a file with a bad extension somehow existed on disk, the route
    # refuses to serve a non-allowlisted type
    import os

    path = os.path.join(_theme_dir(client), "my-theme", "evil.js")
    with open(path, "w") as f:
        f.write("alert(1)")

    resp = client.get("/themes/custom/my-theme/evil.js")

    assert resp.status_code == 404


def test_serving_a_missing_file_404s(client, web_conn):
    _register(client, web_conn)

    resp = client.get("/themes/custom/my-theme/assets/nope.png")

    assert resp.status_code == 404


def test_serving_is_available_without_login(anonymous_client, web_conn):
    _register(anonymous_client, web_conn, css=b":root{ --x: 1; }")

    resp = anonymous_client.get("/themes/custom/my-theme/theme.css")

    assert resp.status_code == 200


def test_media_asset_honors_a_range_request(client, web_conn):
    _register(client, web_conn, assets={"assets/clip.mp3": b"0123456789"})

    resp = client.get("/themes/custom/my-theme/assets/clip.mp3", headers={"Range": "bytes=0-3"})

    assert resp.status_code == 206
    assert resp.headers["Content-Range"] == "bytes 0-3/10"


def test_registered_custom_theme_appears_in_the_switcher(client, web_conn):
    _register(client, web_conn, display_name="Neon Dreams")

    resp = client.get("/preferences")

    assert b"Neon Dreams" in resp.data


def test_selecting_a_custom_theme_links_its_served_stylesheet(client, web_conn):
    _register(client, web_conn, slug="neon", display_name="Neon")

    resp = client.get("/?theme=neon")

    assert b"/themes/custom/neon/theme.css?v=" in resp.data


def test_a_custom_theme_row_without_files_on_disk_is_not_offered(client, web_conn):
    # DB row exists but no bundle on the volume (e.g. lost/rebuilt volume) --
    # must not appear in the switcher, and selecting it falls back to default.
    run(admin_queries.insert_custom_theme(web_conn, slug="ghost", display_name="Ghost"))

    resp = client.get("/?theme=ghost")

    assert b"Ghost" not in resp.data
    assert b"theme-subsilver.css" in resp.data  # fell back to the default


def test_a_deleted_theme_cookie_falls_back_to_default(client, web_conn):
    client.set_cookie("theme", "since-deleted")

    resp = client.get("/")

    assert b"theme-subsilver.css" in resp.data


def test_serving_a_theme_asset_runs_no_database_hooks(client, web_conn, monkeypatch, tmp_path):
    """A custom theme is deliberately a maximalist media bundle, so one page
    paint can pull many assets. Each used to run all three DB-touching
    before_request hooks -- including the full Phase-2 per-user visibility
    computation -- to send a PNG off disk.
    """
    from threadbare.db import queries

    calls: list[str] = []

    original_get_guild = queries.get_guild
    original_get_custom_themes = queries.get_custom_themes

    async def counting_get_guild(conn, guild_id):
        calls.append("get_guild")
        return await original_get_guild(conn, guild_id)

    async def counting_get_custom_themes(conn):
        calls.append("get_custom_themes")
        return await original_get_custom_themes(conn)

    monkeypatch.setattr(queries, "get_guild", counting_get_guild)
    monkeypatch.setattr(queries, "get_custom_themes", counting_get_custom_themes)

    # An ordinary page does run them...
    client.get("/")
    assert "get_guild" in calls
    assert "get_custom_themes" in calls

    calls.clear()

    # ...an asset request does not, even for a slug that doesn't exist (the
    # hooks are skipped before the 404).
    resp = client.get("/themes/custom/whatever/assets/bg.png")

    assert resp.status_code == 404
    assert calls == []
