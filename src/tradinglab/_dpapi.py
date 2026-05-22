"""Windows DPAPI wrapper for encrypting small secrets at rest.

The frozen TradingLab redistributable can't ship the user's
broker credentials in a `.env` file (that would be a security trap
and would prevent multi-user installs from working). The standard
Windows answer is the Data Protection API:
``CryptProtectData`` / ``CryptUnprotectData`` (Crypt32.dll). Each
cipher blob is bound to the current Windows user account — no key
material to manage, and a copied blob can't be decrypted on a
different machine or by a different user.

Why ctypes instead of pywin32
-----------------------------
``pywin32`` exposes the same calls via ``win32crypt.CryptProtectData``
but pulls a ~50 MB native install. We only need two functions; a
dozen lines of ``ctypes`` keep the dependency surface zero.

API
---
* ``is_available() -> bool`` — ``True`` on Windows (any version with
  Crypt32.dll). Always ``False`` on macOS / Linux.
* ``protect(plaintext: bytes, *, scope: str = "user") -> bytes`` —
  encrypt. ``scope="machine"`` uses the local-machine key so the
  blob can be decrypted by any user on the same host (not what we
  want for credentials — keep the default).
* ``unprotect(ciphertext: bytes) -> bytes`` — decrypt; raises
  :class:`DpapiError` on tampering / wrong user / wrong machine.
* ``save_secrets_dict(path, mapping)`` /
  ``load_secrets_dict(path) -> dict`` — convenience for the
  credentials store: JSON-encode + DPAPI-encrypt the dict, with
  atomic write semantics (temp file + ``os.replace``) so a crash
  mid-save cannot leave a half-written blob behind.

Non-goals
---------
* No key rotation — DPAPI handles that internally per the Windows
  user profile.
* No password fallback — if the OS is too old to expose DPAPI (or
  we're on macOS / Linux), the caller falls back to plaintext
  env-var input via the in-app dialog and warns the user.
* No multi-secret schema — the caller decides what the dict keys
  mean. We only handle the encrypt / decrypt / atomic-write.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional


class DpapiError(RuntimeError):
    """Raised when DPAPI encrypt / decrypt fails."""


def is_available() -> bool:
    """Return ``True`` if DPAPI can be called on this host.

    Currently equivalent to ``sys.platform == "win32"``. We don't
    probe Crypt32.dll's presence because every supported Windows
    build (Vista+) ships it; failing the actual API call is rare
    enough that we let the exception path handle it.
    """
    return sys.platform == "win32"


def _load_crypt32():
    """Resolve the two function pointers we need. Raises if unavailable."""
    if not is_available():
        raise DpapiError("DPAPI is only available on Windows")
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    return crypt32, kernel32, _DataBlob


def _to_blob(data: bytes, _DataBlob):
    import ctypes
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob()
    blob.cbData = len(data)
    blob.pbData = ctypes.cast(buf, type(blob.pbData))
    # Keep the buffer alive on the blob so the caller doesn't have
    # to remember to retain it.
    blob._buf = buf  # type: ignore[attr-defined]
    return blob


def _from_blob(blob, kernel32) -> bytes:
    import ctypes
    if blob.pbData is None or blob.cbData == 0:
        return b""
    raw = ctypes.string_at(blob.pbData, blob.cbData)
    # Crypt32 allocates the result via ``LocalAlloc``; the docs require
    # us to call ``LocalFree`` to release it. Failing to do so leaks
    # the buffer per call.
    kernel32.LocalFree(blob.pbData)
    return raw


_ENTROPY_DESC = "tradinglab:credentials:v2"


def _entropy_blob(_DataBlob):
    """Build the DPAPI ``pOptionalEntropy`` blob from :data:`_ENTROPY_DESC`.

    The entropy bytes are mixed into the per-blob key derivation by
    DPAPI; passing the same bytes on encrypt + decrypt is mandatory.
    Wrap the descriptor's UTF-8 bytes in a ``_DataBlob`` and return
    the wrapper (the underlying buffer is kept alive on the wrapper
    so the caller doesn't have to retain it separately).
    """
    return _to_blob(_ENTROPY_DESC.encode("utf-8"), _DataBlob)


def protect(plaintext: bytes, *, scope: str = "user") -> bytes:
    """Encrypt ``plaintext`` with the current user's DPAPI master key.

    ``scope`` is one of:
    * ``"user"`` (default) — only the same Windows user account can
      decrypt. Right answer for credentials.
    * ``"machine"`` — any user on the same machine can decrypt.
      Useful for shared service accounts; do not use for personal
      credentials.

    The :data:`_ENTROPY_DESC` constant is passed as
    ``pOptionalEntropy`` (NOT as ``szDataDescr``) so the
    descriptor actually contributes to the derived key. Previously
    the descriptor was passed as ``szDataDescr``, which makes it
    visible UI metadata only — DPAPI ignores it for crypto. Bumping
    the version suffix (``v1`` → ``v2``) ensures any leftover
    pre-fix blob fails to decrypt and forces the user to re-enter
    credentials once. This is a one-time inconvenience that fixes
    a latent crypto-binding bug.

    Raises :class:`DpapiError` on failure.
    """
    if not is_available():
        raise DpapiError("DPAPI is only available on Windows")
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("protect() expects bytes")
    crypt32, kernel32, _DataBlob = _load_crypt32()

    in_blob = _to_blob(bytes(plaintext), _DataBlob)
    entropy = _entropy_blob(_DataBlob)
    out_blob = _DataBlob()
    flags = 0
    if scope == "machine":
        # CRYPTPROTECT_LOCAL_MACHINE = 0x4
        flags |= 0x4

    import ctypes
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,  # szDataDescr — visible UI metadata only, unused
        ctypes.byref(entropy),
        None, None,
        flags,
        ctypes.byref(out_blob),
    )
    if not ok:
        err = ctypes.windll.kernel32.GetLastError()
        raise DpapiError(f"CryptProtectData failed (err={err})")
    return _from_blob(out_blob, kernel32)


def unprotect(ciphertext: bytes) -> bytes:
    """Decrypt a blob previously produced by :func:`protect`.

    Raises :class:`DpapiError` if the blob is corrupt, tampered with,
    encrypted by a different user, encrypted under an older
    :data:`_ENTROPY_DESC` value, or the OS is too old.
    """
    if not is_available():
        raise DpapiError("DPAPI is only available on Windows")
    if not isinstance(ciphertext, (bytes, bytearray)):
        raise TypeError("unprotect() expects bytes")
    if not ciphertext:
        return b""
    crypt32, kernel32, _DataBlob = _load_crypt32()

    in_blob = _to_blob(bytes(ciphertext), _DataBlob)
    entropy = _entropy_blob(_DataBlob)
    out_blob = _DataBlob()

    import ctypes
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,  # pPromptStruct out-param ignored
        ctypes.byref(entropy),
        None, None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        err = ctypes.windll.kernel32.GetLastError()
        raise DpapiError(f"CryptUnprotectData failed (err={err})")
    return _from_blob(out_blob, kernel32)


# ---------------------------------------------------------------------------
# Convenience: JSON-encoded secrets dict, atomic on-disk persistence
# ---------------------------------------------------------------------------


def save_secrets_dict(path: Path, mapping: Dict[str, str]) -> None:
    """JSON-encode + DPAPI-encrypt ``mapping`` and atomically write to ``path``.

    Atomic semantics: writes to ``<path>.tmp`` then ``os.replace``s
    over the destination. A crash mid-save leaves the prior blob (if
    any) intact.

    Raises :class:`DpapiError` on encryption failure and ``OSError``
    on disk failure.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(mapping, sort_keys=True).encode("utf-8")
    ciphertext = protect(payload)
    # Use NamedTemporaryFile in the same directory so ``os.replace``
    # is a true atomic rename (cross-device renames are not atomic).
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(ciphertext)
        os.replace(tmp_name, str(path))
    except Exception:
        # Best-effort cleanup of the orphan tempfile.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_secrets_dict(path: Path) -> Optional[Dict[str, str]]:
    """Read + DPAPI-decrypt + JSON-decode the blob at ``path``.

    Returns:
        * ``None`` if the file does not exist (first run).
        * A ``dict[str, str]`` on success.

    Raises:
        :class:`DpapiError` on decrypt failure (corrupt / wrong user
        / wrong machine / OS too old). Callers typically catch and
        fall back to prompting the user via the credentials dialog.
    """
    path = Path(path)
    if not path.is_file():
        return None
    blob = path.read_bytes()
    if not blob:
        return {}
    plaintext = unprotect(blob)
    try:
        loaded = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DpapiError(f"decrypted blob is not valid JSON: {e}") from e
    if not isinstance(loaded, dict):
        raise DpapiError("decrypted blob is not a JSON object")
    # Coerce all values to str to match the env-var contract.
    return {str(k): str(v) for k, v in loaded.items()}


__all__ = [
    "DpapiError",
    "is_available",
    "protect",
    "unprotect",
    "save_secrets_dict",
    "load_secrets_dict",
]
