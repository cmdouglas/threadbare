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


def test_parse_maintenance_flags_with_reset_channel():
    args = cli._parse_maintenance_flags(["--reset-channel", "123"])
    assert (args.reset_channel, args.reset_all_channels) == (123, False)


def test_parse_maintenance_flags_with_reset_all_channels():
    args = cli._parse_maintenance_flags(["--reset-all-channels"])
    assert (args.reset_channel, args.reset_all_channels) == (None, True)


def test_parse_maintenance_flags_with_neither_flag():
    args = cli._parse_maintenance_flags([])
    assert (args.reset_channel, args.reset_all_channels) == (None, False)


# argparse exits 2 (the conventional shell "usage error" code) rather than the
# 1 the hand-rolled parser used, and writes its own usage message -- both are
# argparse's standard behaviour, and worth having over a bespoke code/message.
def test_parse_maintenance_flags_rejects_non_numeric_channel_id(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_maintenance_flags(["--reset-channel", "abc"])

    assert exc_info.value.code == 2
    assert "--reset-channel" in capsys.readouterr().err


def test_parse_maintenance_flags_rejects_missing_channel_id(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_maintenance_flags(["--reset-channel"])

    assert exc_info.value.code == 2
    assert "--reset-channel" in capsys.readouterr().err


def test_parse_maintenance_flags_rejects_an_unknown_flag(capsys):
    """New with argparse: the hand-rolled scan silently ignored anything it
    didn't recognise, so a typo'd flag started a normal sync-worker run.
    """
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_maintenance_flags(["--reset-chanel", "123"])

    assert exc_info.value.code == 2


def test_parse_maintenance_flags_rejects_both_flags_together(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_maintenance_flags(["--reset-channel", "123", "--reset-all-channels"])

    assert exc_info.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_parse_maintenance_flags_with_regroup_channel():
    args = cli._parse_maintenance_flags(["--regroup-channel", "123"])
    assert (args.regroup_channel, args.regroup_all) == (123, False)


def test_parse_maintenance_flags_with_regroup_all():
    args = cli._parse_maintenance_flags(["--regroup-all"])
    assert (args.regroup_channel, args.regroup_all) == (None, True)


def test_parse_maintenance_flags_rejects_mixing_a_reset_with_a_regroup(capsys):
    """Every flag in this group is a "do one job and exit" mode, so combining
    two of them has no meaning -- they share one mutually-exclusive group
    rather than one group per feature.
    """
    with pytest.raises(SystemExit) as exc_info:
        cli._parse_maintenance_flags(["--reset-all-channels", "--regroup-all"])

    assert exc_info.value.code == 2
    assert "not allowed with" in capsys.readouterr().err
