"""Unit tests for ``check_credential_store``.

Scope is intentionally narrow: only the startup helper that probes the
encrypted credential store. The full ``CredentialsDialog`` widget needs a Tk
root and is covered by ``test_credentials_dialog_verify.py``.

This helper used to be ``prime_environment_from_dpapi``, and its job was to
decrypt the blob and push every value into ``os.environ`` so that
``data.credentials`` — which only knew how to read the environment — could see
them. ``data.credentials`` now resolves the store as its own layer, so the
injection is gone and **the strongest invariant here is that nothing reaches
``os.environ`` at all**. What survives is the diagnostic sentinel, which
``app.main`` needs in order to tell a boring miss (first launch) from a
suspicious one (blob present but undecryptable — tampered with, copied from
another machine, or corrupted by a bad upgrade).
"""
from __future__ import annotations

import os

import pytest

from tradinglab import _dpapi
from tradinglab.gui import credentials_dialog

_TARGET_KEYS = ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "ALPACA_API_KEY_ID")


@pytest.fixture
def clean_env(monkeypatch):
    """Start with the managed names unset so 'nothing was set' is provable."""
    for key in _TARGET_KEYS:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# DPAPI unavailable
# ---------------------------------------------------------------------------


def test_non_windows_is_noop(monkeypatch):
    """``is_available() -> False`` means absolutely nothing happens."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: False)

    def _boom(*_args, **_kwargs):
        raise AssertionError("the store must not be read when DPAPI is unavailable")

    monkeypatch.setattr(_dpapi, "load_json_object", _boom)

    before = dict(os.environ)
    assert credentials_dialog.check_credential_store() == "dpapi_unavailable"
    assert dict(os.environ) == before, "os.environ must be byte-identical"


# ---------------------------------------------------------------------------
# The point of the redesign: secrets never enter the process environment
# ---------------------------------------------------------------------------


def test_store_contents_are_never_injected_into_environ(monkeypatch, clean_env):
    """The v2 contract. A stored key must NOT appear in ``os.environ``."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object", lambda _p: {
        "version": 2,
        "vendors": {"alpaca": {"fields": {"ALPACA_API_KEY_ID": "from_store"}}},
    })

    assert credentials_dialog.check_credential_store() == "loaded"
    assert "ALPACA_API_KEY_ID" not in os.environ


def test_v1_blob_contents_are_never_injected_either(monkeypatch, clean_env):
    """Upgrade path: a legacy flat blob is read, still never exported."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object",
                        lambda _p: {"SCHWAB_APP_KEY": "legacy"})
    monkeypatch.setattr(_dpapi, "save_json_object", lambda *_a, **_k: None)

    assert credentials_dialog.check_credential_store() == "loaded"
    assert "SCHWAB_APP_KEY" not in os.environ


def test_existing_environ_values_are_left_alone(monkeypatch, clean_env):
    """A real shell export still wins — and is not clobbered by the probe."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object", lambda _p: {
        "version": 2,
        "vendors": {"schwab": {"fields": {"SCHWAB_APP_KEY": "from_store"}}},
    })
    monkeypatch.setenv("SCHWAB_APP_KEY", "from_env")

    credentials_dialog.check_credential_store()
    assert os.environ["SCHWAB_APP_KEY"] == "from_env"


# ---------------------------------------------------------------------------
# Sentinels
# ---------------------------------------------------------------------------


def test_missing_blob_reports_missing(monkeypatch, clean_env):
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object", lambda _p: None)
    assert credentials_dialog.check_credential_store() == "missing"


def test_empty_blob_reports_missing(monkeypatch, clean_env):
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object", lambda _p: {})
    assert credentials_dialog.check_credential_store() == "missing"


def test_store_with_no_values_reports_missing(monkeypatch, clean_env):
    """A record that exists but holds only metadata is not 'loaded'."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object", lambda _p: {
        "version": 2,
        "vendors": {"alpaca": {"fields": {},
                               "last_verified": {"status": "ok",
                                                 "checked_at": 1.0}}},
    })
    assert credentials_dialog.check_credential_store() == "missing"


def test_decrypt_failure_reports_decrypt_error(monkeypatch, clean_env):
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)

    def _raise(_path):
        raise _dpapi.DpapiError("corrupt")

    monkeypatch.setattr(_dpapi, "load_json_object", _raise)
    assert credentials_dialog.check_credential_store() == "decrypt_error"
    for key in _TARGET_KEYS:
        assert key not in os.environ


def test_io_failure_reports_io_error(monkeypatch, clean_env):
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)

    def _raise(_path):
        raise OSError("perm denied")

    monkeypatch.setattr(_dpapi, "load_json_object", _raise)
    assert credentials_dialog.check_credential_store() == "io_error"
    for key in _TARGET_KEYS:
        assert key not in os.environ


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_v1_blob_triggers_migration(monkeypatch, clean_env):
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object",
                        lambda _p: {"ALPACA_API_KEY_ID": "legacy"})
    written: list[dict] = []
    monkeypatch.setattr(_dpapi, "save_json_object",
                        lambda _p, obj: written.append(obj))

    credentials_dialog.check_credential_store()
    assert written, "a v1 blob must be rewritten in the v2 schema"
    assert written[-1]["version"] == 2


def test_v2_blob_is_not_rewritten(monkeypatch, clean_env):
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object", lambda _p: {
        "version": 2,
        "vendors": {"alpaca": {"fields": {"ALPACA_API_KEY_ID": "k"}}},
    })

    def _boom(*_a, **_k):
        raise AssertionError("a v2 store must not be rewritten at startup")

    monkeypatch.setattr(_dpapi, "save_json_object", _boom)
    assert credentials_dialog.check_credential_store() == "loaded"


def test_failed_migration_still_returns_a_sentinel(monkeypatch, clean_env):
    """Migration is best-effort; a read-only data dir must not break startup."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(_dpapi, "load_json_object",
                        lambda _p: {"ALPACA_API_KEY_ID": "legacy"})

    def _raise(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(_dpapi, "save_json_object", _raise)
    assert credentials_dialog.check_credential_store() == "loaded"
