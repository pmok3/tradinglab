# `gui/credentials_dialog.py` — Configure-credentials dialog + DPAPI bootstrap

## Purpose
End users running the frozen `.exe` cannot edit `.env` — there is
no `pyproject.toml` next to the executable for the dotenv
discovery walk to find (`data/credentials.py` short-circuits on
`sys.frozen`). This dialog is the only end-user-visible path for
entering Schwab / Alpaca / Polygon credentials.

On Windows the saved values are encrypted via DPAPI
(`_dpapi.protect`) and persisted to
`%LOCALAPPDATA%\TradingLab\credentials.dat`. On macOS / Linux we
DON'T persist (Keychain / libsecret integration is out of scope
for this iteration) — the dialog still works but values live only
in the current `os.environ`.

## Public API
- `prime_environment_from_dpapi() -> str` — read the DPAPI blob,
  decrypt, and inject every `KEY=VALUE` into `os.environ` (without
  overwriting pre-existing values). Called from `app.main()`
  immediately after `_enable_high_dpi_awareness()` and BEFORE
  `ChartApp()` so the very first vendor-credential read sees the
  values. Returns a **string sentinel** indicating outcome:
  - `"loaded"` — at least one env var was injected from the blob.
  - `"missing"` — no blob file, or empty blob, or every key in the
    blob was already present in `os.environ`.
  - `"dpapi_unavailable"` — `_dpapi.is_available()` is `False`
    (non-Windows host).
  - `"decrypt_error"` — blob exists but `_dpapi.unprotect()`
    raised. Most likely cause: the v1 → v2 entropy bump (audit M1)
    — the user must re-enter credentials once.
  - `"io_error"` — `OSError` reading the blob file.
  - `"import_error"` — `_dpapi` module failed to import (unexpected;
    should not happen in production).
  Never raises. The sentinel is captured by `app.py::main()` which
  surfaces `decrypt_error` and `io_error` to the user via
  `status_log.warn(...)` after the chart app constructs.
- `CredentialsDialog(parent)` — Tk modal with seven entry fields
  (3 Schwab, 3 Alpaca, 1 Polygon) and per-secret "show" toggles.
- `open_credentials_dialog(parent)` — convenience wrapper:
  construct + `wait_window`. Returns the dialog instance (or
  `None` on TclError).

## Fields
Order, label, and `is_secret` flag come from the module-level
`_FIELDS` tuple. Adding a new vendor field is a one-line edit to
that list — the dialog re-builds itself accordingly.

### Vendor gating (`schwab-credentials-always-on`)
Schwab credential fields are surfaced **unconditionally** so a user
wiring up the integration can stash their App Key / Secret /
Redirect URI ahead of the data fetcher landing. The
`_visible_fields()` helper returns `list(_FIELDS)` without any
vendor filter — historically (`schwab-credentials-gated`, retired
2026-05-21) the Schwab rows were suppressed when
`data.schwab_source.SCHWAB_REGISTRATION_ENABLED` was `False`, but
that prevented users from configuring credentials in parallel with
the OAuth plumbing work. The data-source registration is still
gated by `SCHWAB_REGISTRATION_ENABLED` in `data/__init__.py` — the
credentials UI is just persistence, so saving Schwab keys on a
build that hasn't shipped the OAuth flow yet is harmless (the
values sit in the DPAPI blob until the source starts reading them).

Existing DPAPI-stored Schwab keys are NOT erased between launches —
`prime_environment_from_dpapi` always injects them into
`os.environ` on next launch so a future `SCHWAB_REGISTRATION_ENABLED
= True` flip picks them straight up without the user having to
re-enter anything.

## Save semantics
- Empty form + Save → confirm dialog → write empty blob (clears
  prior config).
- Non-empty form + Save → DPAPI-encrypt + atomic-write blob to
  `%LOCALAPPDATA%\TradingLab\credentials.dat`. Apply to current
  `os.environ`. Call `data.credentials.reload()` to refresh the
  in-process cache.
- Cancel → no on-disk change; `os.environ` also untouched.

## Atomic write
Delegates to `_dpapi.save_secrets_dict` which writes a sibling
`<file>.<rand>.tmp` and `os.replace`s. A crash mid-save leaves the
prior blob intact.

## Threat model
DPAPI binds the cipher to the current Windows user account. A
copied `credentials.dat` cannot be decrypted on a different
machine or by a different user. The plaintext exists in
`os.environ` while the process runs — a crash dump that captures
the environment can leak the secret, but that's a property of
every Python program that holds secrets in env vars.

## Modal keys
`__init__` calls `bind_modal_keys(self, cancel=self._on_cancel,
primary=self._on_save)` (ESC dismisses, Return commits).
