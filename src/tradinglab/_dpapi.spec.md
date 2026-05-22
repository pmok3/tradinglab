# `_dpapi.py` — Windows DPAPI ctypes wrapper

## Purpose
Encrypt small secrets (broker credentials) at rest using the
Windows Data Protection API. Each cipher blob is bound to the
current Windows user account — no key material to manage, and a
copied blob can't be decrypted on a different machine or by a
different user.

## Why ctypes (not pywin32)
`pywin32` exposes the same API via `win32crypt.CryptProtectData` but pulls a
~50 MB native install. A few dozen lines of ctypes keep the dependency surface
at zero.

## Public API
- `is_available() -> bool` — `True` on Windows, `False` on macOS/Linux.
- `protect(plaintext: bytes, *, scope: str = "user") -> bytes` — encrypt.
  `scope="machine"` sets `CRYPTPROTECT_LOCAL_MACHINE=0x4`; do not use for
  personal credentials. Raises `DpapiError` on failure.
- `unprotect(ciphertext: bytes) -> bytes` — decrypt. Raises `DpapiError` on
  tamper / wrong user / wrong machine / wrong entropy.
- `save_secrets_dict(path, mapping)` — JSON + DPAPI + atomic write
  (`mkstemp` in dest dir + `os.replace`).
- `load_secrets_dict(path) -> Optional[Dict[str, str]]` — `None` for missing,
  `{}` for empty blob, dict on success. Raises `DpapiError` on decrypt /
  parse failure.
- `DpapiError` — raised on encrypt / decrypt failure.

## Entropy threading (security audit M1)
`CryptProtectData` accepts two distinct parameters often confused:

1. `szDataDescr` — second arg — human-readable label, **stored in
   the blob and shown by the Windows credential UI**. DPAPI does
   NOT use this for crypto. We pass `None` here.
2. `pOptionalEntropy` — third arg — **additional secret bytes mixed
   into the key derivation**. Required at both encrypt and decrypt
   time; mismatched entropy → `DpapiError`. We pass
   `_ENTROPY_DESC = "tradinglab:credentials:v2"` here (UTF-8 bytes
   wrapped in a `_DataBlob`).

Earlier versions of this module passed `_ENTROPY_DESC` as
`szDataDescr` (the wrong slot). That meant the entropy string was
visible metadata but had zero cryptographic effect — any process
running as the same Windows user could decrypt the blob with no
knowledge of the descriptor. The fix threads the same bytes through
`pOptionalEntropy` on both calls.

**Version bump v1 → v2:** any blob written by the old code can NOT
be decrypted by the new code (the entropy on disk is now actively
checked). `gui/credentials_dialog.prime_environment_from_dpapi`
returns the `decrypt_error` sentinel; `app.py::main` surfaces a
status-bar warning; the user re-enters credentials once. After that
single re-entry, the v2 blob is durable.

## Atomicity
`save_secrets_dict` uses `tempfile.mkstemp` in the destination directory
(same filesystem so `os.replace` is a true atomic rename), writes ciphertext,
then renames. On exception the temp file is best-effort cleaned up.

## Memory management
`_DataBlob` results from `CryptProtect/UnprotectData` are allocated by
`LocalAlloc`; docs require a matching `LocalFree`. `_from_blob` always calls
`kernel32.LocalFree` after `string_at` copies the bytes — failing to do so
leaks the buffer per call.

## Testing
- `tests/unit/test_dpapi.py` — Windows-only; skipped on POSIX.
  Covers entropy threading (v2 blobs reject decryption without
  matching entropy), save/load round-trip, missing-file → `None`.
