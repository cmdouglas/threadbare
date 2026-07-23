from .conftest import run


async def _seed_guild(conn, *, guild_id=1, icon=None):
    await conn.execute(
        "INSERT INTO guilds (id, name, icon) VALUES (%s, %s, %s)", (guild_id, "Test Guild", icon)
    )


async def _seed_category(conn, *, category_id, guild_id=1, name="A Category", position=0):
    await conn.execute(
        "INSERT INTO channels (id, guild_id, type, name, position) VALUES (%s, %s, 4, %s, %s)",
        (category_id, guild_id, name, position),
    )


async def _seed_board(
    conn, *, channel_id, guild_id=1, parent_id=None, name="general", position=0, is_public=True
):
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, parent_id, type, name, position, is_public, indexed)
        VALUES (%s, %s, %s, 0, %s, %s, %s, true)
        """,
        (channel_id, guild_id, parent_id, name, position, is_public),
    )


async def _seed_message(conn, *, message_id, channel_id, author_id=100, content="hi", hours_ago=0):
    await conn.execute(
        "INSERT INTO users (id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (author_id, "alice"),
    )
    await conn.execute(
        """
        INSERT INTO messages (id, channel_id, author_id, content, posted_at)
        VALUES (%s, %s, %s, %s, now() - %s * interval '1 hour')
        """,
        (message_id, channel_id, author_id, content, hours_ago),
    )


async def _seed_forum_board(conn, *, channel_id, guild_id=1, name="a forum"):
    await conn.execute(
        """
        INSERT INTO channels (id, guild_id, type, name, is_public, indexed)
        VALUES (%s, %s, 15, %s, true, true)
        """,
        (channel_id, guild_id, name),
    )


async def _seed_thread(conn, *, thread_id, parent_channel_id, name="a thread"):
    await conn.execute(
        "INSERT INTO threads (id, parent_channel_id, name, created_at) VALUES (%s, %s, %s, now())",
        (thread_id, parent_channel_id, name),
    )


def test_board_index_renders_with_no_boards(client, web_conn):
    run(_seed_guild(web_conn))

    resp = client.get("/")

    assert resp.status_code == 200


def test_board_index_shows_a_board_and_its_post_count(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general"))
    run(_seed_message(web_conn, message_id=1000, channel_id=10))

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"general" in resp.data
    assert b"alice" in resp.data


def test_board_index_shows_column_headers_for_posts_and_last_post(client, web_conn):
    run(_seed_guild(web_conn))

    resp = client.get("/")

    assert b'<th class="board-post-count">Posts</th>' in resp.data
    assert b'<th class="board-last-post">Last post</th>' in resp.data


def test_board_index_shows_relative_last_post_time_with_an_exact_tooltip(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general"))
    run(_seed_message(web_conn, message_id=1000, channel_id=10, hours_ago=2))

    resp = client.get("/")

    assert b"2 hours ago" in resp.data
    assert b'title="' in resp.data


def test_board_index_shows_no_pagination_control_for_a_single_page_board(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general"))
    run(_seed_message(web_conn, message_id=1000, channel_id=10))

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"board-pagination-row" not in resp.data


def test_board_index_shows_a_pagination_control_for_a_multi_page_freeform_board(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general"))
    for i in range(26):
        run(_seed_message(web_conn, message_id=1000 + i, channel_id=10, content=f"msg {i}"))

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"board-pagination-row" in resp.data
    assert b'class="pagination-page" href="/board/10/continuous/page/2">2</a>' in resp.data


def test_board_index_shows_a_pagination_control_for_a_multi_page_forum_board(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_forum_board(web_conn, channel_id=10, name="a forum"))
    for i in range(26):
        run(_seed_thread(web_conn, thread_id=3000 + i, parent_channel_id=10, name=f"topic {i}"))

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"board-pagination-row" in resp.data
    assert b'class="pagination-page" href="/board/10/topics?page=2">2</a>' in resp.data


def test_board_index_pagination_control_has_a_jump_to_page_form_for_a_freeform_board(
    client, web_conn
):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="general"))
    for i in range(26):
        run(_seed_message(web_conn, message_id=1000 + i, channel_id=10, content=f"msg {i}"))

    resp = client.get("/")

    assert resp.status_code == 200
    assert b'class="jump-to-page" action="/board/10/continuous/jump_to_page"' in resp.data
    assert b'class="pagination-bar"' in resp.data


def test_board_index_pagination_control_has_a_jump_to_page_form_for_a_forum_board(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_forum_board(web_conn, channel_id=10, name="a forum"))
    for i in range(26):
        run(_seed_thread(web_conn, thread_id=3000 + i, parent_channel_id=10, name=f"topic {i}"))

    resp = client.get("/")

    assert resp.status_code == 200
    assert b'class="jump-to-page" action="/board/10/topics"' in resp.data


def test_board_index_excludes_non_public_boards(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_board(web_conn, channel_id=10, name="secret", is_public=False))

    resp = client.get("/")

    assert b"secret" not in resp.data


def test_board_index_groups_boards_under_their_category(client, web_conn):
    run(_seed_guild(web_conn))
    run(_seed_category(web_conn, category_id=1, name="Main"))
    run(_seed_board(web_conn, channel_id=10, parent_id=1, name="general"))

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"Main" in resp.data
    assert b"general" in resp.data


def test_board_index_shows_guild_name_in_title_and_header(client, web_conn):
    run(_seed_guild(web_conn))

    resp = client.get("/")

    assert b"<title>Test Guild (threadbare view)</title>" in resp.data
    assert b'class="site-title"' in resp.data
    assert b"Test Guild (threadbare view)" in resp.data


def test_board_index_falls_back_to_threadbare_when_guild_is_unknown(client):
    resp = client.get("/")

    assert b"<title>Threadbare</title>" in resp.data


def test_board_index_shows_guild_icon_in_masthead_when_set(client, web_conn):
    run(_seed_guild(web_conn, icon="abcdef"))

    resp = client.get("/")

    assert b'class="site-icon"' in resp.data
    assert b"https://cdn.discordapp.com/icons/1/abcdef.png" in resp.data


def test_board_index_shows_no_icon_in_masthead_when_guild_has_none(client, web_conn):
    run(_seed_guild(web_conn))

    resp = client.get("/")

    assert b'class="site-icon"' not in resp.data
