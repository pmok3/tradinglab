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
- `check_credential_store() -> str` — probe the encrypted credential
  store and perform the one-time v1 → v2 schema migration. Called from
  `app.main()` immediately after `_enable_high_dpi_awareness()` and
  BEFORE `ChartApp()`.

  **It injects nothing.** This function used to be
  `prime_environment_from_dpapi`, whose job was to decrypt the blob and
  push every `KEY=VALUE` into `os.environ` so that `data.credentials` —
  which only knew how to read the environment — could see them.
  `credentials._build_layers` now resolves the store as its own layer,
  ranked below a real shell export and above the plaintext files, which
  is exactly where priming placed it. Dropping the injection keeps
  secrets out of the process environment, where they were reachable by
  crash dumps, subprocesses, and any library that logs `os.environ`.

  What remains is the diagnostic sentinel `app.main()` needs to tell a
  boring miss from a suspicious one:
  - `"loaded"` — store decrypted and holds at least one credential.
  - `"missing"` — no blob file, empty blob, or records with no values.
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
**`ALPACA_TIER`** — the "Alpaca data plan" selector (`Free — IEX feed
(15-min delayed), 200 req/min` / `Paid — SIP feed (real-time), unlimited
req/min`), which **replaced the
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

Existing stored Schwab keys are NOT erased between launches — they stay
in the encrypted store and `data.credentials` resolves them on every
launch, so a future `SCHWAB_REGISTRATION_ENABLED = True` flip picks them
straight up without the user having to re-enter anything.

## Save semantics
- Empty form + Save → confirm dialog → the affected vendors are cleared
  from the store.
- Non-empty form + Save → `_persist_to_store(values)` writes **per
  vendor** via `credential_store.save_vendor`, leaving other vendors
  untouched. A vendor whose credential fields are all empty is cleared
  outright rather than left as a metadata-only husk — note the readonly
  plan combo always reports a value, so "is there a secret?" is judged
  excluding `_CHOICE_FIELDS`. Then `_clear_primed_environment` pops any
  managed name a **pre-v2** session injected, and
  `data.credentials.reload()` refreshes the in-process cache.
- Non-Windows (no DPAPI) → `_apply_session_only(values)` writes
  `os.environ` for this process only, and says so in a message box.
- Cancel → no on-disk change; `os.environ` also untouched.

## Atomic write
Delegates to `_dpapi.save_json_object` (via `credential_store`) which
writes a sibling `<file>.<rand>.tmp` and `os.replace`s. A crash
mid-save leaves the prior blob intact.

## Pre-fill reads the resolved layers, not `os.environ`
`_populate_fields` calls `credentials.effective_values()`. Reading the
environment directly was correct only while the store was primed into
it; once priming stopped, an environment read rendered every stored
credential as an empty box — the "my keys vanished" failure. The
environment is now just one of four layers.

## Threat model
DPAPI binds the cipher to the current Windows user account. A
copied `credentials.dat` cannot be decrypted on a different
machine or by a different user. **Secrets are no longer written into
`os.environ`** — the process still holds them in memory (unavoidable),
but they are out of the environment block that crash dumps and child
processes inherit.

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

## "Test connection" — credential verification

Each vendor section with a registered verifier (Alpaca, Polygon today; not
Schwab, which has none) gets a `[Test connection]` button plus a status line
and a wrapped remediation line. The button answers the question a new user
actually has after pasting a key: *is this thing going to work?*

### Verifies what is TYPED, not what is saved

`_vendor_credentials_from_form(vendor)` builds the vendor credential object
from the current entry contents via
`data.credentials.build_credentials(form_values.get)` — it does **not** read
`os.environ` or `get_credentials()`. The user must be able to paste a key,
test it, fix a typo and re-test without committing a bad blob to the DPAPI
store. Going through the shared builder also guarantees the probe derives
the same `tier` → `feed` mapping the app will actually request.

Empty required fields short-circuit to `not_configured` with no thread and
no network.

### Worker thread + Tk polling (NOT cross-thread `after(0, ...)`)

`_begin_verify` starts a daemon thread that writes only into a local
handoff dict (`_verify_boxes[vendor]`), then schedules
`_poll_verify` every `_VERIFY_POLL_MS` (100 ms) on the Tk thread.

**Do not "simplify" this into `self.after(0, ...)` from the worker.** Stock
CPython on Windows ships a non-threaded Tcl; a cross-thread `after` raises
`RuntimeError("main thread is not in main loop")` and the callback is
silently dropped, leaving the button stuck on "Testing…" forever. Same
contract as the strategy-tester background export (see
`gui/strategy_tab.spec.md` and CLAUDE.md §7.15). A source-grep test pins it.

While in flight the button is disabled and relabelled; re-entrant clicks are
ignored. A worker that dies without publishing a result is detected via
`thread.is_alive()` and reported as `error` rather than hanging the UI.
`<Destroy>` cancels pending poll jobs; the daemon threads touch nothing that
outlives the dialog, so they are safe to abandon.

### Result rendering

`_STATUS_STYLE` maps every `verify.ALL_STATUSES` member to a (glyph, colour)
pair — a test asserts total coverage so a new status cannot fall through to
a silent muted dash.

`forbidden` / `rate_limited` / `network_error` render **amber, not red**: in
all three the credentials may be perfectly fine, and a red ✗ would send the
user off re-copying a key that was never the problem. Only
`invalid_credentials` and `error` are red.

### Stale-verdict invalidation

Every credential field''s `textvariable` is traced; any change resets that
vendor''s line to "Not tested yet.". A lingering green "✓ Ready" next to a
changed key is the single most misleading state this dialog could show.

Traced rather than `<KeyRelease>`-bound on purpose: a keyboard binding
misses right-click → Paste (no key event fires at all), which is exactly how
a user swaps in a key copied off a vendor dashboard. A `_populating` flag
suppresses the trace during the initial pre-fill.

**Two Tk lifetime hazards, both load-bearing:**

1. Every field's `StringVar` is retained in `_field_vars`. A ttk widget
   stores only the Tcl variable *name*; if the Python `StringVar` is
   collected, `Variable.__del__` unsets the Tcl variable and silently
   destroys every trace on it. Invalidation would then stop firing at an
   unpredictable GC boundary — a stale green checkmark that appears only
   sometimes. Pinned by `test_field_vars_survive_garbage_collection`,
   which asserts `trace info variable` is non-empty after `gc.collect()`.
2. All Tk variables are constructed with an explicit `master=self`. Without
   it they bind to `tkinter._default_root`, and traces can attach to a stale
   interpreter when more than one root exists in a process.

### Save applies immediately — no restart

`_close_and_refresh()` does four things in order: `credentials.reload()`,
`verify.clear_results()` (cached verdicts described the *previous*
credentials), `data.register_vendor_sources()`, then the `on_changed`
callback (wired by `help_menu` to `_refresh_data_source_combobox`).

If the set of user-visible sources changed, a dialog names the entries that
just became — or stopped being — available, pointing at the toolbar source
dropdown. That is the concrete "your data source is ready for use" signal.
It is silent when nothing changed, so a no-op save never nags.

### Sizing

The added test rows push the dialog past the small-screen safe height, so
the form body is wrapped in `make_scrollable_form` and
`protect_combobox_wheel` receives the canvas as `scroll_target` (§7.11 — the
form both scrolls and contains a Combobox).

Tests: `tests/unit/gui/test_credentials_dialog_verify.py`.
## Removing credentials is symmetric with adding

Two paths, because there are two storage backends.

`_persist_to_store(values)` is the real one on Windows. It walks
`_SECTIONS` and either saves that vendor's subset or calls
`credential_store.clear_vendor`. A vendor is cleared when **no
credential field** survives — judged excluding `_CHOICE_FIELDS`, since
`ALPACA_TIER` is a readonly combobox that always reports a value and
would otherwise keep a husk record alive forever.

`_apply_session_only(values)` is the non-DPAPI fallback: it both **sets**
present values and **removes** managed names absent from `values`.
`_clear_primed_environment(values)` is the same removal half applied
after a successful store write, to undo what a **pre-v2** session
injected — without it a key the user just deleted would keep resolving
from the stale environment entry (which outranks the store) for the rest
of the session, and the source would stay in the dropdown making
authenticated requests with a credential the user believes they revoked.

Only names in `_managed_env_names()` (the `_FIELDS` keys) are ever
touched; unrelated environment variables are never modified.

`_has_credential_values(values)` ignores `_CHOICE_FIELDS` when deciding
whether the form is "empty". `ALPACA_TIER` is a readonly combobox that
*always* reports a value, so the older `if not values` test could never be
true and the "this clears your saved credentials" confirmation was dead
code.

### When a file still supplies a "deleted" key

`_populate_from_environment` reads `os.environ` only — it does not see a
plaintext `alpaca.txt` / `credentials.txt` or a dev `.env`. For a user whose
credential lives in such a file the fields render blank, and this dialog
cannot clear that layer.

`_warn_if_file_backed()` therefore fires only when a field that **had
content at open** is now empty *and* the vendor still resolves as
configured. That is precisely the confusing case ("I deleted it and the
source is still there"), and the message names the file mechanism plus
Help → Reveal Data Folder. A setup that opened blank is the steady state for
file-backed users and is left silent — warning on every save would be noise.
`_initial_values` is the open-time snapshot that distinguishes the two.

**Test hygiene:** `tests/unit/gui/test_credentials_dialog_verify.py` stubs
`showinfo` / `showwarning` / `showerror` in an autouse fixture. A real modal
opened from a test blocks until a human clicks it — the pytest timeout is
the only thing that ends the run.