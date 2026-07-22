import logging

from threadbare.logging_config import DEFAULT_LOG_FORMAT, configure_logging


def _patched_basic_config(monkeypatch):
    calls = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: calls.append(kwargs))
    return calls


def test_configure_logging_defaults_to_info_level(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    calls = _patched_basic_config(monkeypatch)

    configure_logging()

    assert calls == [{"level": logging.INFO, "format": DEFAULT_LOG_FORMAT}]


def test_configure_logging_respects_log_level_env_var(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    calls = _patched_basic_config(monkeypatch)

    configure_logging()

    assert calls == [{"level": logging.DEBUG, "format": DEFAULT_LOG_FORMAT}]


def test_configure_logging_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warning")
    calls = _patched_basic_config(monkeypatch)

    configure_logging()

    assert calls == [{"level": logging.WARNING, "format": DEFAULT_LOG_FORMAT}]


def test_configure_logging_falls_back_to_info_for_an_invalid_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "not-a-real-level")
    calls = _patched_basic_config(monkeypatch)

    configure_logging()

    assert calls == [{"level": logging.INFO, "format": DEFAULT_LOG_FORMAT}]
