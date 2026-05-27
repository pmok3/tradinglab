# gui/dialogs.py — Spec

## Purpose
Modal Tk dialogs owned by `ChartApp`: the Settings dialog (worker count, dark mode, log price axis) and the Watchlists dialog (CRUD + import/export). Split out of `app.py` to keep that file focused on chart + data orchestration.

## Public API
- `WORKER_COUNT_MIN = 1`, `WORKER_COUNT_MAX = 64` — clamp bounds. Re-exported from `app.py` as class attrs on `ChartApp` so `WorkerPoolMixin._clamp_worker_count` can reach them without importing this module.
- `_prompt_string(parent, title, prompt, initial="") -> Optional[str]` — thin wrapper around `simpledialog.askstring`. Exists as a test seam (monkey-patchable in smoke checks).
- `class _SettingsDialog(BaseModalDialog)`:
  - Shows a worker-thread spinbox, a "Dark mode" checkbox, a "Logarithmic price axis" checkbox, an "Invert scroll-zoom direction" checkbox, a "Volume time-of-day shading" checkbox, a **Startup parameters** `LabelFrame`, a **Display timezone** `LabelFrame`, and a **Theme customization** hint frame whose body is a short label + an `Open Theme Editor…` button. The Startup section also contains `Check for updates on startup` and an optional update endpoint override entry. The per-slot color grid (light/dark swatches + presets) moved to the dedicated `ThemeEditorDialog` (`gui/theme_editor.py`, big-bet item #7); the button here is a one-click fallback for users who reach Settings out of habit. `_swatch_buttons` is kept as an empty per-mode dict for back-compat with legacy tests that introspect it.
  - **Startup parameters** has one row per entry in `constants.STARTUP_DEFAULT_KEYS`. `interval` / `source` / `theme` use readonly `ttk.Combobox` (values pulled from `INTERVAL_PERIODS.keys()`, `DATA_SOURCES.keys()`, and `("light","dark")`); `ticker` / `compare` use `ttk.Entry`. Two helper buttons sit below the rows: **"Use current chart"** (`_on_capture_current_as_default` — copies the live `ticker_var` / `compare_ticker_var` / `interval_var` / `source_var` / `dark_var` values into the dialog's StringVars) and **"Reset to builtins"** (`_on_reset_startup_defaults` — sets every dialog StringVar back to `BUILTIN_STARTUP_DEFAULTS`). A muted hint label clarifies "Applied next launch — does not change the current session." The same section includes the splash checkbox, the update-check-on-startup checkbox, and a free-form update endpoint override (empty = built-in/ENV fallback). Persistence happens at "Save and Close" (`_commit_startup_defaults` walks `STARTUP_DEFAULT_KEYS` and calls `parent.set_startup_default(key, value)` for each; `_on_ok` writes the splash/update tunables).
  - **Display timezone** sits between Startup parameters and Theme customization: a single `ttk.Combobox` (`state="normal"`, free-form) backed by `_tz_var`. Curated dropdown values: `["", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "UTC", "Europe/London", "Europe/Berlin", "Asia/Tokyo", "Asia/Singapore", "Australia/Sydney"]` — but the user can type any IANA name. Empty string clears the override (back to ET-native). Initial value snapshotted to `_tz_initial` from `parent._display_tz` at dialog open; commit at OK calls `parent.set_display_tz(new_tz)` only if changed (skips an otherwise-unnecessary `_render`). Cancel discards the dialog Tk var (no live-preview state to revert).
  - Dark-mode and log-price toggles **preview live** (via `_on_dark_toggle` / `_on_log_toggle` which call `parent._apply_theme()` / `parent._apply_price_scale()`). Color swatch picks are also live. Startup-parameter edits, update-check edits, and timezone selection are **not** live in the dialog — they only land at "Save and Close". (Timezone is then applied immediately by `set_display_tz`'s render+refill.) **Cancel reverts** to the initial state (stored in `_dark_initial` / `_log_initial` / `_overrides_initial` / `_startup_initial` / `_tz_initial`, the override and startup snapshots being a `deepcopy` of the parent dicts captured at dialog open). Cancel is wired to both the button and `WM_DELETE_WINDOW`. The primary footer button is labelled **"Save and Close"** (renamed from "OK" in the dialog-paradigm sweep — audit `dialog-button-paradigms`) and commits the worker count, the startup-defaults dict, update-check tunables, and (if changed) the display timezone; theme override state was already applied live.
  - Helpers: `_on_open_theme_editor` (lazily imports `gui.theme_editor.open_theme_editor` and routes to the dedicated dialog), `_build_startup_defaults_section(parent)`, `_on_capture_current_as_default()`, `_on_reset_startup_defaults()`, `_commit_startup_defaults()`. The legacy in-dialog theme grid helpers (`_build_theme_editor_section`, `_current_color`, `_refresh_swatches`, `_on_pick_color`, `_on_reset_themes`) were removed when the grid moved to `theme_editor.py`.
- `class _WatchlistDialog(BaseModalDialog)`:
  - **Two-pane layout**: names Treeview on the left (`[Pin][Name]` columns; a ✓ in the Pin column marks pinned lists), tickers Listbox on the right.
  - Buttons: `New`, `Rename`, `Delete` (for names); `Pin`, `Unpin` (toggle pin state of the selected list); `Add`, `Remove` (for tickers); `Import…`, `Export…` (JSON round-trip via `watchlists/storage.export_to_file` / `import_from_file`, mode `"merge"` on import).
  - Bottom row exposes two dismissal actions: **`Save and Close`** (primary — persists watchlists via `ChartApp._on_menu_save_watchlists` then closes) and **`Close`** (discard-on-exit). `Save and Close` falls through to a save-as file picker when the manager has no `loaded_path()`; if the user cancels that picker the dialog stays open so in-flight changes aren't dropped.
  - `Pin` button is disabled when (a) no name is selected, (b) the selected name is already pinned, or (c) the manager is at `MAX_PINNED` capacity. `Unpin` is disabled when the selected name is not pinned.
  - On close (Close button, window X, `WM_DELETE_WINDOW`, or the post-save tail of `Save and Close`), if any pin state changed during the session, calls `parent._rebuild_watchlist_subtabs()` so the sub-tab strip reflects the new pin set.
  - Errors surface via `messagebox.showerror(..., parent=self)` so modals stack correctly.

## Dependencies
- Internal: `..watchlists.WatchlistManager`, `..watchlists.export_to_file/import_from_file`.
- External: `tkinter`, `tkinter.ttk`, `tkinter.simpledialog`, `tkinter.filedialog`, `tkinter.messagebox`.

## Design notes
- Both dialogs inherit from `gui._modal_base.BaseModalDialog`, which
  owns `transient` / `grab_set` / ESC+Return keybindings / geometry
  persistence (geometry keys `dlg.settings` / `dlg.watchlists`) via
  the trailing `_finalize_modal(primary=..., cancel=...)` call. The
  cancel-revert semantics for `_SettingsDialog` live in the
  `_on_cancel` override (restores `_overrides_initial` /
  `_startup_initial` snapshots, then `destroy`s); `_WatchlistDialog`
  uses `_on_close` as the cancel callback so a WM_DELETE / ESC
  dismissal still hits the pin-rebuild path.
- Live preview for theme and log-price toggles (instant feedback). Cancel
  reverts by re-applying the initial snapshot; OK is a no-op for these
  (already applied). Worker count is committed on OK only.
- Name CRUD + ticker CRUD + pin CRUD live in one dialog (matches user mental
  model). Pin cap is enforced both in the manager (raises `ValueError` at
  capacity) and via dialog button disable (prevents the modal alert).
- Rebuild-on-close (not per-toggle) — three pin toggles do one
  `_rebuild_watchlist_subtabs`. Gated by `_pin_dirty`.
- Names column is a Treeview (Listbox lacks columns); tickers stay a Listbox.
- Import is merge-by-default; the manager accepts `mode="replace"` but the
  dialog doesn't expose it. Imported pin lists ARE merged into manager pins
  (de-duped, capped at `MAX_PINNED`).
- `_prompt_string` exists so smoke tests can stub it.
- `WORKER_COUNT_MIN/MAX` live here (the UI layer is the one place users see
  them); the mixin re-exposes them on `ChartApp` to avoid importing this.

## Invariants
- `_SettingsDialog._on_cancel` fully reverts dark mode, log-price preview,
  theme overrides, and startup defaults (from `_overrides_initial` /
  `_startup_initial` snapshots — `deepcopy` of parent dicts).
- `_SettingsDialog._on_ok` writes the worker count exactly once and commits
  the startup-defaults dict via `_commit_startup_defaults` (one
  `parent.set_startup_default` call per key in `STARTUP_DEFAULT_KEYS`).
  Theme overrides were persisted live; OK is a no-op for them.
- `_SettingsDialog` installs the **Combobox / Spinbox wheel-guard**
  dialog-wide at the end of `__init__` via
  `protect_combobox_wheel(self, scroll_target=self._form_canvas)`.
  Without this, the dialog's `canvas.bind_all("<MouseWheel>", …)`
  global scroll handler would let wheel events fall through to the
  ttk Combobox/Spinbox class binding, silently mutating the worker
  spinbox / UI scale combobox / startup-defaults comboboxes / pin-cap
  spinbox values on every wheel tick. See CLAUDE.md §7.11 and
  `tests/unit/gui/test_settings_dialog_wheel_guard.py`. Settings is
  built fully in `__init__` with no partial rebuilds, so a single
  call after the last widget is created is sufficient.
- `_WatchlistDialog` errors surface as modals and abort the specific action
  (never leaves the manager in a broken state).
- `_WatchlistDialog._on_close` calls `parent._rebuild_watchlist_subtabs()`
  exactly once iff `_pin_dirty`, wired to both the Close button and
  `WM_DELETE_WINDOW`.
- `_WatchlistDialog._on_save_and_close` routes through
  `ChartApp._on_menu_save_watchlists` so the recent-files list,
  loaded-path tracking, and dirty-title suffix stay consistent with
  `Watchlists -> Save Watchlists`. If the watchlist set has never been
  saved (no `loaded_path()`) and the user cancels the resulting Save As
  file picker, the dialog stays open so the in-flight changes aren't
  silently discarded.
- Tk-main-thread-only — all widget construction/mutation occurs on the Tk
  thread (cross-thread via `self.after`).
