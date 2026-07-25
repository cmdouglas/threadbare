import io
import os
import shutil
import zipfile

from threadbare.theme_bundle import REQUIRED_CUSTOM_PROPERTIES

from .conftest import E2E_GUILD_ID


def _theme_bundle_bytes() -> bytes:
    props = "\n".join(f"  {p}: initial;" for p in sorted(REQUIRED_CUSTOM_PROPERTIES))
    css = ":root {\n" + props + "\n}\nbody { background: url(assets/bg.png); }\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("theme.css", css)
        zf.writestr("assets/bg.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


def test_mod_registers_a_custom_theme_then_it_renders_and_downloads(
    anonymous_page, live_server, seed_conn
):
    anonymous_page.context.add_cookies(
        [live_server.session_cookie(user_id=1, display_name="mod", is_mod=True)]
    )
    theme_dir = live_server.app.config["SETTINGS"].theme_storage_dir
    try:
        # Upload a real bundle through the admin page's file input.
        anonymous_page.goto(f"{live_server}/admin/themes")
        anonymous_page.locator("input[name=display_name]").fill("E2E Theme")
        anonymous_page.locator("input[name=bundle]").set_input_files(
            {"name": "e2e.zip", "mimeType": "application/zip", "buffer": _theme_bundle_bytes()}
        )
        anonymous_page.locator(".admin-theme-register button[type=submit]").click()
        assert "E2E Theme" in anonymous_page.content()  # now listed

        # It appears in the preferences switcher...
        anonymous_page.goto(f"{live_server}/preferences")
        assert "E2E Theme" in anonymous_page.content()

        # ...and selecting it links the served stylesheet, whose referenced
        # asset actually loads.
        anonymous_page.goto(f"{live_server}/?theme=e2e-theme")
        assert "/themes/custom/e2e-theme/theme.css" in anonymous_page.content()
        asset = anonymous_page.request.get(f"{live_server}/themes/custom/e2e-theme/assets/bg.png")
        assert asset.status == 200

        # Download round-trips as a .zip.
        download = anonymous_page.request.get(f"{live_server}/admin/themes/e2e-theme/download")
        assert download.status == 200
        assert download.headers["content-type"] == "application/zip"
    finally:
        with seed_conn.cursor() as cur:
            cur.execute("DELETE FROM custom_themes WHERE slug = 'e2e-theme'")
        seed_conn.commit()
        shutil.rmtree(os.path.join(theme_dir, "e2e-theme"), ignore_errors=True)


CHANNEL_ID = 900300
GATED_CHANNEL_ID = 900301

VIEW_CHANNEL = 1 << 10
READ_MESSAGE_HISTORY = 1 << 16
BOTH_REQUIRED = VIEW_CHANNEL | READ_MESSAGE_HISTORY


def _seed_channel(conn):
    conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (E2E_GUILD_ID, "E2E Guild"),
    )
    conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, 0, 'admin-test-channel', true, true) ON CONFLICT DO NOTHING
        """,
        (CHANNEL_ID, E2E_GUILD_ID),
    )
    conn.commit()


def _cleanup_channel(conn):
    conn.execute("DELETE FROM channels WHERE id = %s", (CHANNEL_ID,))
    conn.commit()


def _seed_gated_channel(conn, *, everyone_permissions):
    conn.execute(
        "INSERT INTO guilds (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (E2E_GUILD_ID, "E2E Guild"),
    )
    conn.execute(
        "INSERT INTO roles (id, guild_id, name, color, position, permissions) "
        "VALUES (%s, %s, '@everyone', 0, 0, %s) "
        "ON CONFLICT (id) DO UPDATE SET permissions = EXCLUDED.permissions",
        (E2E_GUILD_ID, E2E_GUILD_ID, everyone_permissions),
    )
    conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed, visibility_enrolled)
        VALUES (%s, %s, 0, 'gated-test-channel', false, true, true) ON CONFLICT DO NOTHING
        """,
        (GATED_CHANNEL_ID, E2E_GUILD_ID),
    )
    conn.commit()


def _cleanup_gated_channel(conn):
    conn.execute("DELETE FROM channels WHERE id = %s", (GATED_CHANNEL_ID,))
    conn.execute("DELETE FROM roles WHERE id = %s", (E2E_GUILD_ID,))
    conn.commit()


def test_anonymous_visit_redirects_to_login(anonymous_page, live_server):
    # /login itself immediately redirects onward to Discord's real OAuth
    # authorize page (web/views/auth.py's `login` route), so an anonymous
    # visit's final landing spot is discord.com, not our own /login URL.
    anonymous_page.goto(f"{live_server}/")

    assert anonymous_page.url.startswith("https://discord.com/oauth2/authorize")


def test_logged_in_non_mod_can_browse_but_gets_403_on_admin(anonymous_page, live_server):
    anonymous_page.context.add_cookies(
        [live_server.session_cookie(user_id=1, display_name="member", is_mod=False)]
    )

    anonymous_page.goto(f"{live_server}/")
    assert anonymous_page.url == f"{live_server.base_url}/"

    response = anonymous_page.goto(f"{live_server}/admin/")
    assert response.status == 403


def test_logged_in_mod_can_toggle_channel_indexed_flag_end_to_end(
    anonymous_page, live_server, seed_conn
):
    _seed_channel(seed_conn)
    try:
        anonymous_page.context.add_cookies(
            [live_server.session_cookie(user_id=1, display_name="mod", is_mod=True)]
        )

        anonymous_page.goto(f"{live_server}/admin/")
        row = anonymous_page.locator(".admin-channel-row", has_text="admin-test-channel")
        assert "yes" in row.locator(".admin-channel-indexed").inner_text()

        row.locator('form[action*="toggle-indexed"] button').click()

        with seed_conn.cursor() as cur:
            cur.execute("SELECT indexed FROM channels WHERE id = %s", (CHANNEL_ID,))
            assert cur.fetchone()["indexed"] is False
    finally:
        _cleanup_channel(seed_conn)


def test_enrolled_channel_visible_only_once_a_role_grants_access(
    anonymous_page, live_server, seed_conn
):
    # Full stack, real browser: the before_request hook -> the
    # board.py/board_index.py gate -> the query-level visibility clause.
    _seed_gated_channel(seed_conn, everyone_permissions=0)
    try:
        anonymous_page.context.add_cookies(
            [live_server.session_cookie(user_id=1, display_name="member", is_mod=False)]
        )

        anonymous_page.goto(f"{live_server}/")
        assert "gated-test-channel" not in anonymous_page.content()

        response = anonymous_page.goto(f"{live_server}/board/{GATED_CHANNEL_ID}")
        assert response.status == 404

        with seed_conn.cursor() as cur:
            cur.execute(
                "UPDATE roles SET permissions = %s WHERE id = %s",
                (BOTH_REQUIRED, E2E_GUILD_ID),
            )
        seed_conn.commit()

        anonymous_page.goto(f"{live_server}/")
        assert "gated-test-channel" in anonymous_page.content()

        # A text channel's landing page redirects to continuous browsing --
        # Playwright follows the redirect, so a 200 here confirms the final
        # page rendered, not just that the redirect itself was issued.
        response = anonymous_page.goto(f"{live_server}/board/{GATED_CHANNEL_ID}")
        assert response.status == 200
    finally:
        _cleanup_gated_channel(seed_conn)
