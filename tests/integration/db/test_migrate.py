import hashlib
from pathlib import Path

import psycopg

from threadbare.db.migrate import (
    Migration,
    apply_migration,
    run_migrations,
)
from threadbare.db.migrate import _applied_migrations as applied_migrations
from threadbare.db.migrate import _ensure_schema_migrations_table as ensure_schema_migrations_table


def _fixture_migration(version: str, sql: str) -> Migration:
    checksum = hashlib.sha256(sql.encode()).hexdigest()
    return Migration(version=version, path=Path(f"{version}.sql"), checksum=checksum, sql=sql)


async def test_apply_migration_creates_table_and_records_version(test_database_url):
    # db.migrate manages its own connection with the default (tuple) row
    # factory, independent of the app's dict_row convention used by the
    # shared db_conn fixture — so its own internals are tested against the
    # same tuple-row shape they actually run with in production.
    conn = await psycopg.AsyncConnection.connect(test_database_url, autocommit=False)
    try:
        await ensure_schema_migrations_table(conn)
        migration = _fixture_migration(
            "9999_integration_test_fixture",
            "CREATE TABLE integration_test_fixture_table (id int)",
        )

        await apply_migration(conn, migration)

        applied = await applied_migrations(conn)
        assert applied["9999_integration_test_fixture"] == migration.checksum

        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM integration_test_fixture_table")
            assert await cur.fetchall() == []
    finally:
        await conn.rollback()
        await conn.close()


async def test_run_migrations_against_real_schema_is_idempotent(test_database_url):
    first_run = await run_migrations(test_database_url)
    second_run = await run_migrations(test_database_url)

    assert second_run == []
    assert isinstance(first_run, list)


async def test_initial_schema_tables_exist(db_conn):
    async with db_conn.cursor() as cur:
        await cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
        """)
        tables = {row["table_name"] for row in await cur.fetchall()}

    expected = {
        "guilds",
        "channels",
        "threads",
        "users",
        "messages",
        "attachments",
        "reactions",
        "sync_state",
        "schema_migrations",
    }
    assert expected <= tables
