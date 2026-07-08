# gui/local_data_dialog.py — Spec

## Purpose
The dialog opened by **Tools → Configure Local Data…**. Lets the user
manage BYOD (Bring Your Own Data) roots — folders on disk **or zip
archives** whose subfolders (or top-level zip directories) become
entries in the source-selector combobox.

## Public API
- `LocalDataDialog(parent, *, on_changed=None)` — `BaseModalDialog`
  modal to `parent`. The optional `on_changed` callback is invoked
  after a successful Save and Close so the caller can refresh the
  source combobox.
- `open_local_data_dialog(parent, *, on_changed=None) -> LocalDataDialog`
  — thin convenience opener used by the Tools menu callback.
- `_validate_root_name(name) -> Optional[str]` — module-level validator
  exposed for unit-tests. Returns `None` if valid, else an error
  message. Rules: non-empty, alphanumerics + underscores only (no
  hyphens — hyphens are reserved for the `<root_name>-<subdir>` combobox
  key separator).

## Module-level helpers
- `_load_roots_from_settings() -> (enabled, [(name, path), ...])` —
  reads the `local_data` settings key.
- `_save_roots_to_settings(enabled, roots) -> None` — persists the
  same key via `tradinglab.settings.set`.
- `_refresh_data_registry()` — calls `defaults.reload()` so the cached
  `local_data` value is dropped, strips every hyphen-containing
  non-builtin source key from `DATA_SOURCES`, then calls
  `register_local_sources()` to re-register from the new settings.

## Dependencies
- Internal: `tradinglab.settings`, `tradinglab.defaults`,
  `tradinglab.data.DATA_SOURCES`, `tradinglab.data.register_local_sources`,
  `._modal_base.BaseModalDialog`,
  `._modal_base.protect_combobox_wheel`, `._modal_keys.bind_modal_keys`,
  `.colors.MUTED_GREY`.
- External: `tkinter`, `tkinter.ttk`, `tkinter.filedialog`,
  `tkinter.messagebox`.

## Design Decisions
- **Save and Close / Cancel paradigm**. Composing a definition (root
  list) — not adjusting a live setting — so the user expects a deliberate
  commit. Matches the `dialog-button-paradigms` audit ID. The parent
  dialog inherits `BaseModalDialog` and applies the combobox wheel guard
  after building widgets.
- **Enabled checkbox separate from row list**. So a user can keep a
  configured root list around but temporarily disable BYOD without
  losing the configuration.
- **Name validator: alphanumerics + underscores only**. Hyphens are
  reserved as the separator in combobox keys (`<root-name>-<subdir>`);
  allowing a hyphen in the root name would make any downstream parser
  ambiguous.
- **Per-root path must exist at save time**. Validation runs before
  `_save_roots_to_settings` and blocks the save with a messagebox if
  any configured root path is neither a directory nor a `.zip` file.
  (If `enabled=False`, validation is skipped — a disabled config with
  stale paths is fine.)
- **Zip-as-root** (audit `local-source-zip`). `_prompt_for_root.
  _browse` asks the user whether they're picking a folder or a `.zip`
  file. When the chosen path is a zip, `discover_subsources` walks
  the archive's top-level directories and builds `make_local_zip_fetcher`
  fetchers — no extraction needed.
- **Add / Edit launches a modal sub-dialog**. `_prompt_for_root` runs a
  `wait_window` so the user can't interact with the parent until they
  commit or cancel the row edit.
- **Confirmation messagebox for Remove**. Removing a root is a
  destructive action from the user's mental model even though no files
  are deleted; explicit confirmation prevents accidents.
- **On save: strip BYOD entries from `DATA_SOURCES` BEFORE
  re-registration**. Removed roots vanish from the combobox; renamed
  roots get new keys; nothing leaks across edits. Built-in source keys
  (yfinance, synthetic, alpaca, polygon, …) are NEVER stripped.

## Invariants
- A successfully-saved root list always satisfies: every name is
  alphanumeric+underscore, every path is a directory or `.zip` file at
  save time, every name is unique within the list.
- After `_on_save`, either both the settings AND the data registry
  reflect the new roots, OR the settings reflect them but the registry
  refresh failed with a visible error message (the dialog still
  closes; the user can re-open it to retry).
- `_on_cancel` MUST NOT mutate settings or `DATA_SOURCES`.

## Testing
`tests/unit/gui/test_local_data_dialog.py` — 18 tests covering:
- `_refresh_data_registry` strips only hyphen-containing non-builtin
  keys; handles empty BYOD lists without raising.
- Dialog lifecycle: opens / closes, settings load into Treeview, Cancel
  doesn't invoke save, Save invokes save + `on_changed` callback.
- Name validator: 7 valid forms (parametrized), 6 invalid forms with
  expected error-substring matches (hyphen, alphanumeric, required).

## Known limitations
- **No drag-to-reorder rows**. Roots are processed in list order; if
  two roots' subfolders happened to share a name (impossible today
  because the `<root_name>-<subdir>` key prefixes by name) the
  later-registered one would win. The unique-name rule + composite
  combobox key means this can't actually happen.
- **No "Verify" button**. Users can't validate a root's contents
  (e.g. how many `(ticker, interval)` pairs it'll register) without
  saving and looking at the source combobox. A future preview pane is
  reserved.
- **No watch on file-system changes**. Adding a new CSV to a configured
  root requires either app restart OR opening this dialog and clicking
  Save again (re-runs `register_local_sources`).
