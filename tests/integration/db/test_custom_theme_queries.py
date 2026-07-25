from threadbare.db import admin_queries, queries


async def test_insert_and_get_custom_theme(db_conn):
    await admin_queries.insert_custom_theme(db_conn, slug="my-theme", display_name="My Theme")

    row = await admin_queries.get_custom_theme(db_conn, "my-theme")

    assert row["slug"] == "my-theme"
    assert row["display_name"] == "My Theme"


async def test_get_custom_theme_returns_none_when_absent(db_conn):
    assert await admin_queries.get_custom_theme(db_conn, "nope") is None


async def test_delete_custom_theme(db_conn):
    await admin_queries.insert_custom_theme(db_conn, slug="my-theme", display_name="My Theme")

    await admin_queries.delete_custom_theme(db_conn, "my-theme")

    assert await admin_queries.get_custom_theme(db_conn, "my-theme") is None


async def test_insert_replaces_an_existing_slug(db_conn):
    await admin_queries.insert_custom_theme(db_conn, slug="my-theme", display_name="First")

    await admin_queries.insert_custom_theme(db_conn, slug="my-theme", display_name="Second")

    row = await admin_queries.get_custom_theme(db_conn, "my-theme")
    assert row["display_name"] == "Second"


async def test_touch_custom_theme_bumps_updated_at(db_conn):
    await admin_queries.insert_custom_theme(db_conn, slug="my-theme", display_name="My Theme")
    await db_conn.execute(
        "UPDATE custom_themes SET updated_at = updated_at - interval '1 hour' "
        "WHERE slug = 'my-theme'"
    )
    rewound = (await admin_queries.get_custom_theme(db_conn, "my-theme"))["updated_at"]

    await admin_queries.touch_custom_theme(db_conn, "my-theme")

    after = (await admin_queries.get_custom_theme(db_conn, "my-theme"))["updated_at"]
    assert after > rewound


async def test_get_custom_themes_lists_all_ordered_by_display_name(db_conn):
    await admin_queries.insert_custom_theme(db_conn, slug="zebra", display_name="Zebra")
    await admin_queries.insert_custom_theme(db_conn, slug="apple", display_name="Apple")

    rows = await queries.get_custom_themes(db_conn)

    assert [r["slug"] for r in rows] == ["apple", "zebra"]
    assert "updated_at" in rows[0]


async def test_get_custom_themes_empty(db_conn):
    assert await queries.get_custom_themes(db_conn) == []
