import hashlib
from pathlib import Path

import psycopg
import pytest

from threadbare.db.migrate import (
    Migration,
    MigrationError,
    apply_migration,
    check_schema_up_to_date,
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


async def test_check_schema_up_to_date_passes_when_everything_applied(test_database_url):
    # The real migrations directory is already fully applied against
    # test_database_url by the time any test runs (test_run_migrations_...
    # above proves that) -- so this should be a silent no-op.
    await check_schema_up_to_date(test_database_url)


async def test_check_schema_up_to_date_raises_when_a_migration_is_pending(
    test_database_url, tmp_path
):
    # Points at a throwaway directory containing one migration that's
    # nowhere in test_database_url's real schema_migrations table -- proves
    # the "forgot to run threadbare-migrate" case fails loudly rather than
    # being silently ignored. Read-only: never calls apply_migration, so
    # this never actually creates the table it names.
    (tmp_path / "9999_not_yet_applied.sql").write_text("CREATE TABLE never_applied (id int)")

    with pytest.raises(MigrationError, match="9999_not_yet_applied"):
        await check_schema_up_to_date(test_database_url, directory=tmp_path)


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
