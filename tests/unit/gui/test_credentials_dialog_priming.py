"""Unit tests for ``prime_environment_from_dpapi``.

Scope is intentionally narrow: only the pure-helper that injects
DPAPI-decrypted secrets into ``os.environ``. The full
``CredentialsDialog`` widget needs a Tk root and is deferred (see
``test-coverage-audit.md`` §5).

Key invariants exercised here:

* On non-Windows hosts (``_dpapi.is_available()`` -> ``False``) the
  helper is a strict no-op and returns ``"dpapi_unavailable"``.
* Existing ``os.environ`` values are **never** overwritten — a shell
  ``$env:`` set in front of the launcher must always win over the
  DPAPI blob (security contract per
  ``credentials_dialog.spec.md``). Missing keys *are* filled in.
* All foreseeable failure modes (``DpapiError``, ``OSError``, blob
  absent) are reported via a distinct string sentinel; the helper
  never propagates an exception, so a corrupt credentials store
  never crashes startup. The caller (``app.main``) surfaces
  ``"decrypt_error"`` / ``"io_error"`` on the status bar so the
  user notices a present-but-unreadable blob (could be tampered
  with, copied from a different machine, or an upgrade-corruption
  artefact).
"""
from __future__ import annotations

import os

from tradinglab import _dpapi
from tradinglab.gui import credentials_dialog

# ---------------------------------------------------------------------------
# Test 1 — non-Windows / DPAPI unavailable
# ---------------------------------------------------------------------------


def test_prime_environment_on_non_windows_is_noop(monkeypatch):
    """``is_available() -> False`` means absolutely nothing happens."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: False)

    # If the helper ever reaches load_secrets_dict it would explode here.
    def _boom(*_args, **_kwargs):
        raise AssertionError(
            "load_secrets_dict must not be called when DPAPI is unavailable"
        )

    monkeypatch.setattr(_dpapi, "load_secrets_dict", _boom)

    before = dict(os.environ)
    result = credentials_dialog.prime_environment_from_dpapi()
    after = dict(os.environ)

    assert result == "dpapi_unavailable"
    assert after == before, "os.environ must be byte-identical on no-op path"


# ---------------------------------------------------------------------------
# Test 2 — existing values are preserved, missing keys are filled
# ---------------------------------------------------------------------------


def test_prime_environment_does_not_overwrite_existing_values(monkeypatch):
    """Security invariant: shell-provided env vars always win over DPAPI."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)
    monkeypatch.setattr(
        _dpapi,
        "load_secrets_dict",
        lambda _path: {
            "SCHWAB_APP_KEY": "from_dpapi",
            "SCHWAB_APP_SECRET": "from_dpapi",
        },
    )

    # SCHWAB_APP_KEY already set in the environment — must NOT be touched.
    monkeypatch.setenv("SCHWAB_APP_KEY", "from_env")
    # SCHWAB_APP_SECRET intentionally absent — should be filled in.
    monkeypatch.delenv("SCHWAB_APP_SECRET", raising=False)

    result = credentials_dialog.prime_environment_from_dpapi()

    assert result == "loaded", "at least one key was injected, so loaded is expected"
    assert os.environ["SCHWAB_APP_KEY"] == "from_env", (
        "existing env value must be preserved (security invariant)"
    )
    assert os.environ["SCHWAB_APP_SECRET"] == "from_dpapi", (
        "missing keys must be filled in from the DPAPI blob"
    )


# ---------------------------------------------------------------------------
# Test 3 — every failure mode is swallowed; nothing is set on failure
# ---------------------------------------------------------------------------


def test_prime_environment_swallows_dpapi_errors(monkeypatch):
    """Corrupt blob, permission denied, or missing file all return a sentinel."""
    monkeypatch.setattr(_dpapi, "is_available", lambda: True)

    # Ensure the target keys start unset so we can prove "nothing was set".
    target_keys = ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET")
    for key in target_keys:
        monkeypatch.delenv(key, raising=False)

    # --- 3a: DpapiError (decryption failure / wrong user / wrong machine)
    def _raise_dpapi(_path):
        raise _dpapi.DpapiError("corrupt")

    monkeypatch.setattr(_dpapi, "load_secrets_dict", _raise_dpapi)
    assert credentials_dialog.prime_environment_from_dpapi() == "decrypt_error"
    for key in target_keys:
        assert key not in os.environ, (
            f"{key} must remain unset after a DpapiError"
        )

    # --- 3b: OSError (e.g. permission denied reading the blob)
    def _raise_os(_path):
        raise OSError("perm denied")

    monkeypatch.setattr(_dpapi, "load_secrets_dict", _raise_os)
    assert credentials_dialog.prime_environment_from_dpapi() == "io_error"
    for key in target_keys:
        assert key not in os.environ, (
            f"{key} must remain unset after an OSError"
        )

    # --- 3c: missing-file case — load_secrets_dict returns None
    monkeypatch.setattr(_dpapi, "load_secrets_dict", lambda _path: None)
    assert credentials_dialog.prime_environment_from_dpapi() == "missing"
    for key in target_keys:
        assert key not in os.environ, (
            f"{key} must remain unset when no blob exists yet"
        )
