import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg

DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str
    sql: str


def discover_migrations(directory: Path = DEFAULT_MIGRATIONS_DIR) -> list[Migration]:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        sql = path.read_text()
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        migrations.append(Migration(version=path.stem, path=path, checksum=checksum, sql=sql))
    return migrations


def pending_migrations(discovered: list[Migration], applied: dict[str, str]) -> list[Migration]:
    """`applied` maps version -> checksum of already-applied migrations.

    Raises MigrationError if an applied migration's file has changed since
    it was applied — edited-after-applied migrations fail loudly rather than
    silently reapplying or skipping.
    """
    pending = []
    for migration in discovered:
        if migration.version in applied:
            if applied[migration.version] != migration.checksum:
                raise MigrationError(
                    f"Migration {migration.version} has changed since it was applied "
                    "(checksum mismatch). Do not edit applied migrations — add a new one."
                )
            continue
        pending.append(migration)
    return pending


async def _ensure_schema_migrations_table(conn: psycopg.AsyncConnection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version text PRIMARY KEY,
            checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


async def _applied_migrations(conn: psycopg.AsyncConnection) -> dict[str, str]:
    async with conn.cursor() as cur:
        await cur.execute("SELECT version, checksum FROM schema_migrations")
        rows = await cur.fetchall()
    return dict(rows)


async def apply_migration(conn: psycopg.AsyncConnection, migration: Migration) -> None:
    await conn.execute(migration.sql)
    await conn.execute(
        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
        (migration.version, migration.checksum),
    )


async def run_migrations(dsn: str, directory: Path = DEFAULT_MIGRATIONS_DIR) -> list[str]:
    discovered = discover_migrations(directory)
    applied_versions: list[str] = []
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await _ensure_schema_migrations_table(conn)
        applied = await _applied_migrations(conn)
        to_apply = pending_migrations(discovered, applied)
        for migration in to_apply:
            async with conn.transaction():
                await apply_migration(conn, migration)
            applied_versions.append(migration.version)
    return applied_versions


def main() -> None:
    import asyncio
    import os
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        raise SystemExit(1)

    applied = asyncio.run(run_migrations(dsn))
    if applied:
        print("Applied migrations:")
        for version in applied:
            print(f"  - {version}")
    else:
        print("No pending migrations.")


if __name__ == "__main__":
    main()
