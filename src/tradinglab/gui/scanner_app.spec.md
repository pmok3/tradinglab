# `gui/scanner_app.py` — Scanner-tab construction + per-row action routing

## Purpose

`ScannerAppMixin` is a `ChartApp` mixin extracted in wave-3 of the
`app.py` god-file shrink (CLAUDE §7.24). Owns the six methods that
bridge the right-side Scanner notebook page to the rest of the
application: tab construction, save/delete callbacks for the editor,
per-row action routing, and two thin delegates that forward
scanner-related sandbox refresh / reset calls to
`self._sandbox_ctrl` (a `backtest.sandbox_app.SandboxAppController`).

The mixin owns NO instance state. State lives on `ChartApp`:

- `self._notebook` — the right-side `ttk.Notebook` the Scanner tab is
  added to.
- `self._scanner_storage` — assigned in `_build_scanner_tab` (a
  reference to the `tradinglab.scanner.storage` module).
- `self._scan_runner` — a `ScanRunner` instance held for the lifetime
  of the ChartApp.
- `self._scan_tick_id` / `self._scan_last_results` — bookkeeping
  used by polling sites in other ChartApp methods.
- `self._scanner_tab` — the mounted `ScannerTab` widget.
- `self._sandbox_ctrl`, `self._is_sandbox_active`, `self.ticker_var`,
  `self.compare_var`, `self.compare_ticker_var`, `self.watchlist_var`,
  `self._watchlist_manager`, `self._populate_watchlist_tab`,
  `self._load_data`, `self._status` — pre-existing ChartApp surface
  the methods read/write.

## Public surface

- `ScannerAppMixin._build_scanner_tab() -> None` — autoload library +
  mount the Scanner tab. Failure to load saved scans degrades to an
  empty library with a user-visible warning (`_status.warn`); never
  crashes the app boot.
- `ScannerAppMixin._on_scanner_scan_saved(scan) -> None` — persist via
  `self._scanner_storage.save`. Errors surface as `_status.error`
  with `{scan.name!r}`.
- `ScannerAppMixin._on_scanner_scan_deleted(scan_id: str) -> None` —
  delete from storage AND clear the runner's per-scan history so a
  recreated scan starts fresh.
- `ScannerAppMixin._on_scanner_row_action(symbol: str, kind: str)` —
  `kind in {"primary", "compare", "watchlist"}`. Routes through the
  sandbox `_sandbox_register_and_focus` / `_sandbox_register_compare`
  paths when a session is active; falls back to direct `ticker_var`
  / `compare_ticker_var` / watchlist append otherwise. Tolerates
  every dependency being absent (best-effort logging via
  `_status.error`).
- `ScannerAppMixin._refresh_scanner_for_sandbox() -> None` and
  `_reset_scanner_state() -> None` — 1-line forwarders to the
  `SandboxAppController` methods of the same name, passing
  `silent_tcl` from the module-local helper.

## Mixin contract (§7.24)

1. NO `__init__`, NO `super().__init__()`. Instance state lives on
   `ChartApp.__init__`.
2. Inserted alphabetically among the mixin block in the `ChartApp`
   MRO declaration.
3. `tk.Tk` stays last.
4. Colocated `.spec.md` (this file).
5. Source-grep tests that read `app.py` were extended to also scan
   `gui/scanner_app.py` where they reference moved code.

## `_silent_tcl` helper

A module-local `contextmanager` clone of the same-named helper in
`app.py`. Swallows `tk.TclError` plus any extra exception classes
passed positionally. Owned here (not back-imported from `app`)
because:

- back-importing `tradinglab.app` would create a circular import
  chain through every ChartApp mixin;
- the helper is tiny and stateless;
- the sandbox controller delegates accept a `silent_tcl` callable
  injected by the caller — symmetric with how `_sandbox_ctrl`
  methods receive it from `app.py` today.

## Tests

- `tests/unit/test_chartapp_wave3_extraction.py` — structural tests
  pinning the mixin file exists, owns the documented methods, has
  no `__init__`, is in MRO alphabetically, and the moved methods
  are no longer present in the direct `ChartApp` body.
- `tests/scanner/test_app_wiring.py` — runtime tests covering
  `_on_scanner_scan_saved`, `_on_scanner_scan_deleted`, and
  `_on_scanner_row_action` end-to-end via `app._method()` calls;
  these continue to pass unchanged because mixin methods are
  inherited normally.
