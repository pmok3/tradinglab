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
- `CredentialsDialog(parent)` — `BaseModalDialog` modal with eight
  credential fields (3 Schwab, 4 Alpaca, 1 Polygon) and per-secret
  "show" toggles.
- `open_credentials_dialog(parent)` — convenience wrapper:
  construct + `wait_window`. Returns the dialog instance (or
  `None` on TclError).

## Fields
Order, label, and `is_secret` flag come from the module-level
`_FIELDS` tuple. Adding a new vendor field is a one-line edit to
that list — the dialog re-builds itself accordingly.

### Constrained dropdown fields (`_CHOICE_FIELDS`)
Fields listed in `_CHOICE_FIELDS` render as a **read-only
`ttk.Combobox`** instead of a free-text entry, mapping a friendly
display label ↔ a stored env value; optional muted helper text under
the control comes from `_CHOICE_HELP`. Today the only choice field is
**`ALPACA_TIER`** — the "Alpaca data plan" selector (`Free — IEX feed,
200 req/min` / `Paid — SIP feed, 10,000 req/min`), which **replaced the
old free-text `ALPACA_FEED` field** (tier-UX council decision). Making
the plan the single control prevents the #1 misconfig — plan/feed
disagreement (`paid`+`iex` → silently partial volume; `free`+`sip` →
403s) — because `data.credentials` derives `feed` from `tier`. Round-trip:
`_populate_from_environment` maps the stored value → display (default =
first/`Free` when unset or unrecognised); `_collect` maps the selected
display → stored value. The combobox is covered by the
`protect_combobox_wheel` guard applied at the end of `__init__`.

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

## Sizing (resizable + content-derived `minsize`)
Opens at `default_geometry="600x660"`, `resizable=(True, True)`, and after
layout (`_build_widgets` + `_populate_from_environment` + wheel guard) sets
`self.minsize(max(540, reqwidth+16), max(480, reqheight+16))` from the
*actual* laid-out request size. The dialog packs three sections (8 fields, a
dropdown-with-help, a multi-line status line, buttons) that overflowed the old
fixed `560x420` **non-resizable** window — the bottom (Polygon field, status,
buttons) clipped on the reporter's Windows-on-ARM display (font/DPI scaling)
with no way to enlarge. Deriving `minsize` from the request size makes the
floor self-correcting under any font / DPI scaling (higher DPI ⇒ larger
request ⇒ larger `minsize`), so the window can never open smaller than its
content; resizable so the user can grow it; the persisted `dlg.credentials`
geometry is bounded below by `minsize` (the WM clamps a stale-small saved size
— e.g. the old `560x420` — back up). Mirrors `sandbox_dialog` (see its spec.md
"Sizing" note). Pinned by `tests/unit/gui/test_credentials_dialog_sizing.py`
(audit `credentials-dialog-sizing`).

## Modal keys and wheel guard
`__init__` calls `protect_combobox_wheel(self)` and then
`BaseModalDialog._finalize_modal(primary=self._on_save,
cancel=self._on_cancel)`. ESC dismisses, Return commits, and built
combobox / spinbox descendants are guarded against wheel-driven
value changes.
