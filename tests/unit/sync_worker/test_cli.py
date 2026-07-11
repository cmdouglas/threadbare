"""--version must short-circuit before any config/DB access -- the rest of
sync_worker/cli.py's boot sequence (schema check, pool, Discord client)
needs a real Postgres connection, so it lives in
tests/integration/sync_worker/test_cli.py instead.
"""

import pytest

import threadbare
from threadbare.sync_worker import cli


def test_main_version_flag_prints_version_and_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["threadbare-sync-worker", "--version"])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 0
    assert threadbare.__version__ in capsys.readouterr().out
