from pathlib import Path

import pytest

from threadbare.db.migrate import MigrationError, discover_migrations, pending_migrations


def _write(dir_path: Path, name: str, sql: str) -> Path:
    path = dir_path / name
    path.write_text(sql)
    return path


def test_discover_migrations_returns_sorted_by_filename(tmp_path):
    _write(tmp_path, "0002_second.sql", "SELECT 2;")
    _write(tmp_path, "0001_first.sql", "SELECT 1;")

    migrations = discover_migrations(tmp_path)

    assert [m.version for m in migrations] == ["0001_first", "0002_second"]


def test_discover_migrations_ignores_non_sql_files(tmp_path):
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    (tmp_path / "README.md").write_text("not a migration")

    migrations = discover_migrations(tmp_path)

    assert [m.version for m in migrations] == ["0001_first"]


def test_pending_migrations_returns_all_when_none_applied(tmp_path):
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    discovered = discover_migrations(tmp_path)

    pending = pending_migrations(discovered, applied={})

    assert [m.version for m in pending] == ["0001_first"]


def test_pending_migrations_skips_already_applied_with_matching_checksum(tmp_path):
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    discovered = discover_migrations(tmp_path)
    applied = {"0001_first": discovered[0].checksum}

    pending = pending_migrations(discovered, applied)

    assert pending == []


def test_pending_migrations_raises_on_checksum_mismatch(tmp_path):
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    discovered = discover_migrations(tmp_path)
    applied = {"0001_first": "stale-checksum"}

    with pytest.raises(MigrationError, match="0001_first"):
        pending_migrations(discovered, applied)
