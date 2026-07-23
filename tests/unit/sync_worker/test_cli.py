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


def test_parse_reset_flags_with_reset_channel():
    assert cli._parse_reset_flags(["--reset-channel", "123"]) == (123, False)


def test_parse_reset_flags_with_reset_all_channels():
    assert cli._parse_reset_flags(["--reset-all-channels"]) == (None, True)


def test_parse_reset_flags_with_neither_flag():
    assert cli._parse_reset_flags([]) == (None, False)


def test_parse_reset_flags_rejects_non_numeric_channel_id(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_reset_flags(["--reset-channel", "abc"])

    assert exc_info.value.code == 1
    assert "numeric" in capsys.readouterr().err


def test_parse_reset_flags_rejects_missing_channel_id(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_reset_flags(["--reset-channel"])

    assert exc_info.value.code == 1
    assert "numeric" in capsys.readouterr().err


def test_parse_reset_flags_rejects_both_flags_together(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_reset_flags(["--reset-channel", "123", "--reset-all-channels"])

    assert exc_info.value.code == 1
    assert "mutually exclusive" in capsys.readouterr().err
