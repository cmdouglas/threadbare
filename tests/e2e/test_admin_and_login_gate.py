from .conftest import E2E_GUILD_ID

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
