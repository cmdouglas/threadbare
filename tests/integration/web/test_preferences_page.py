from datetime import UTC, datetime

from .conftest import run


async def _seed_guild_channel_and_thread(conn):
    await conn.execute("INSERT INTO guilds (id, name) VALUES (%s, %s)", (1, "Test Guild"))
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, is_public) "
        "VALUES (%s, %s, 0, 'general', true)",
        (10, 1),
    )
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, %s)",
        (3000, 10, "a thread", datetime(2026, 1, 1, tzinfo=UTC)),
    )


def test_preferences_page_shows_default_state(client):
    resp = client.get("/preferences")

    assert resp.status_code == 200
    assert b'class="preference-current">subsilver</strong>' in resp.data
    assert b"Hide avatars" in resp.data
    assert b'class="preference-current">25</strong>' in resp.data


def test_preferences_page_theme_query_param_sets_cookie_and_marks_it_current(client):
    resp = client.get("/preferences?theme=plain")

    assert resp.status_code == 200
    assert b'class="preference-current">plain</strong>' in resp.data
    set_cookie_headers = resp.headers.get_all("Set-Cookie")
    assert any("theme=plain" in header for header in set_cookie_headers)


def test_preferences_page_avatars_query_param_sets_cookie_and_switches_label(client):
    resp = client.get("/preferences?avatars=off")

    assert resp.status_code == 200
    assert b"Show avatars" in resp.data
    set_cookie_headers = resp.headers.get_all("Set-Cookie")
    assert any("show_avatars=off" in header for header in set_cookie_headers)


def test_preferences_page_posts_per_page_query_param_sets_cookie_and_marks_it_current(client):
    resp = client.get("/preferences?posts_per_page=50")

    assert resp.status_code == 200
    assert b'class="preference-current">50</strong>' in resp.data
    set_cookie_headers = resp.headers.get_all("Set-Cookie")
    assert any("posts_per_page=50" in header for header in set_cookie_headers)


def test_masthead_links_to_preferences_page_instead_of_inline_toggles(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert b'href="/preferences"' in resp.data
    assert b"theme-switcher" not in resp.data
    assert b"avatar-toggle" not in resp.data


def test_topic_page_no_longer_has_an_inline_posts_per_page_switcher(client, web_conn):
    run(_seed_guild_channel_and_thread(web_conn))

    resp = client.get("/topic/3000/page/1")

    assert resp.status_code == 200
    assert b"posts-per-page-switcher" not in resp.data
