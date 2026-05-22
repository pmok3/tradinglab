"""Unit tests for :mod:`tradinglab._dpapi`.

Locks in the secrets-at-rest invariants:

* :func:`is_available` is a cross-platform ``bool`` predicate
  (``True`` only on Windows).
* :func:`protect` / :func:`unprotect` round-trip arbitrary bytes and
  produce a fresh ciphertext per call (DPAPI uses a random IV /
  system salt — encrypting the same plaintext twice MUST yield
  different blobs).
* Both functions reject non-``bytes`` input with :class:`TypeError`
  (defensive — a stray ``str`` would silently take the wrong code
  path through ``ctypes``).
* :func:`save_secrets_dict` writes atomically via ``tempfile.mkstemp``
  + ``os.replace`` and leaves no ``<name>.<rand>.tmp`` orphan, even
  when the rename step fails.
* :func:`load_secrets_dict` covers the file-state matrix:
  missing file → ``None``; empty-dict blob → ``{}``; non-object
  JSON → :class:`DpapiError`; int values → string-coerced on load;
  garbage bytes → :class:`DpapiError` that names the
  decryption / unprotect failure.

The ctypes-backed round-trip tests need a live Crypt32.dll, so
every test except ``test_is_available_returns_bool_on_any_platform``
is skipped on POSIX via :func:`_dpapi.is_available`.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from tradinglab import _dpapi


_REQUIRES_DPAPI = pytest.mark.skipif(
    not _dpapi.is_available(),
    reason="DPAPI is Windows-only; Crypt32.dll not available on this platform",
)


# ---------------------------------------------------------------------------
# is_available — runs everywhere
# ---------------------------------------------------------------------------


def test_is_available_returns_bool_on_any_platform():
    result = _dpapi.is_available()
    assert isinstance(result, bool)
    if sys.platform != "win32":
        assert result is False
    else:
        assert result is True


# ---------------------------------------------------------------------------
# protect / unprotect round-trip
# ---------------------------------------------------------------------------


@_REQUIRES_DPAPI
def test_protect_unprotect_round_trip_windows_only():
    plaintext = b"hello world"
    ciphertext = _dpapi.protect(plaintext)
    assert isinstance(ciphertext, bytes)
    assert ciphertext  # non-empty
    assert ciphertext != plaintext
    assert _dpapi.unprotect(ciphertext) == plaintext

    # DPAPI is a randomized AEAD: encrypting the same plaintext twice
    # MUST yield distinct ciphertexts. If this regresses we'd be
    # leaking equality of secrets across saves.
    second = _dpapi.protect(plaintext)
    assert second != ciphertext
    assert _dpapi.unprotect(second) == plaintext


# ---------------------------------------------------------------------------
# protect / unprotect type guard
# ---------------------------------------------------------------------------


@_REQUIRES_DPAPI
@pytest.mark.parametrize("bad_input", ["a string", None])
def test_protect_rejects_non_bytes(bad_input):
    with pytest.raises(TypeError):
        _dpapi.protect(bad_input)  # type: ignore[arg-type]


@_REQUIRES_DPAPI
@pytest.mark.parametrize("bad_input", ["a string", None])
def test_unprotect_rejects_non_bytes(bad_input):
    with pytest.raises(TypeError):
        _dpapi.unprotect(bad_input)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# save_secrets_dict atomicity
# ---------------------------------------------------------------------------


@_REQUIRES_DPAPI
def test_save_secrets_dict_atomic_write_leaves_no_tmp_file(tmp_path, monkeypatch):
    target = tmp_path / "secrets.bin"

    _dpapi.save_secrets_dict(target, {"a": "1", "b": "2"})

    # (a) destination exists and is non-empty
    assert target.is_file()
    assert target.stat().st_size > 0

    # (b) no `<name>.<rand>.tmp` orphans left behind on the happy path
    orphans = list(tmp_path.glob("secrets.bin.*.tmp"))
    assert orphans == [], f"unexpected tmp orphans after success: {orphans}"

    # Failure path: monkeypatch `os.replace` to raise. The production
    # code must (1) propagate the error to the caller and (2) clean
    # up the temp file it created — otherwise repeated failures would
    # litter the user's profile directory.
    def _failing_replace(*args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr("os.replace", _failing_replace)

    with pytest.raises(OSError, match="simulated rename failure"):
        _dpapi.save_secrets_dict(target, {"x": "y"})

    orphans_after_fail = list(tmp_path.glob("secrets.bin.*.tmp"))
    assert orphans_after_fail == [], (
        f"rollback should delete the tmp file; found: {orphans_after_fail}"
    )

    # The pre-existing destination file must still be intact (atomic
    # semantics — a failed save MUST NOT corrupt the previous blob).
    assert target.is_file()
    assert _dpapi.load_secrets_dict(target) == {"a": "1", "b": "2"}


# ---------------------------------------------------------------------------
# load_secrets_dict file-state matrix
# ---------------------------------------------------------------------------


@_REQUIRES_DPAPI
def test_load_secrets_dict_paths(tmp_path):
    # ---- 1. Missing file → None (first-run signal to the caller). ----
    missing = tmp_path / "nope.bin"
    assert _dpapi.load_secrets_dict(missing) is None

    # ---- 2. Empty-dict blob round-trips to {}. ----
    empty_path = tmp_path / "empty.bin"
    _dpapi.save_secrets_dict(empty_path, {})
    assert _dpapi.load_secrets_dict(empty_path) == {}

    # ---- 3. Non-object JSON (top-level list) → DpapiError. ----
    list_path = tmp_path / "list.bin"
    list_path.write_bytes(_dpapi.protect(json.dumps([1, 2, 3]).encode("utf-8")))
    with pytest.raises(_dpapi.DpapiError):
        _dpapi.load_secrets_dict(list_path)

    # ---- 4. Int values get coerced to str on load. ----
    int_path = tmp_path / "ints.bin"
    int_path.write_bytes(
        _dpapi.protect(json.dumps({"x": 1, "y": 42}).encode("utf-8"))
    )
    loaded = _dpapi.load_secrets_dict(int_path)
    assert loaded == {"x": "1", "y": "42"}
    # And critically the values are *strings*, not ints — the env-var
    # contract downstream expects str.
    assert all(isinstance(v, str) for v in loaded.values())
    assert all(isinstance(k, str) for k in loaded.keys())

    # ---- 5. Garbage bytes → DpapiError mentioning unprotect/decrypt. ----
    garbage_path = tmp_path / "garbage.bin"
    garbage_path.write_bytes(b"\x00\x01\x02not a valid DPAPI blob\xff\xfe")
    with pytest.raises(_dpapi.DpapiError) as excinfo:
        _dpapi.load_secrets_dict(garbage_path)
    msg = str(excinfo.value).lower()
    assert ("unprotect" in msg) or ("decrypt" in msg), (
        f"DpapiError message must distinguish a decrypt failure from a "
        f"JSON-parse failure; got: {excinfo.value!r}"
    )
