# `gui/help_menu.py` — Help cascade for the ChartApp menubar

## Purpose

`HelpMenuMixin` adds a `Help` cascade as the last menu in the
`ChartApp` menubar. Surfaces version (About), data folder, update
check, onboarding banner, credentials, and reset / purge.

## Public API

- `HelpMenuMixin._build_help_menu(menubar: tk.Menu) -> tk.Menu` —
  build + attach the cascade. Called from
  `ChartApp._build_menubar` immediately before
  `self.config(menu=menubar)`.
- `DOCS_URL: str = ""` — when set, `View Online Docs` opens it via
  `webbrowser.open(url, new=2, autoraise=True)`. Empty → falls
  back to `_on_help_getting_started` (never a dead button).

### Command callbacks (`self._on_help_*`)

- `_on_help_about` — modal info dialog: version + platform + data
  folder path.
- `_on_help_view_online_docs` — opens `DOCS_URL` when set; falls
  back to bundled doc handoff. Last-resort: messagebox with the
  URL to copy-paste. Never raises.
- `_on_help_export_diagnostic_bundle` —
  `filedialog.asksaveasfilename` (default
  `tradinglab-diagnostics-YYYYMMDD-HHMMSS.zip`), then
  `diagnostics.build_diagnostic_bundle(out_path)`. Summary
  `messagebox.showinfo` with path + log / crash counts. Errors
  → `messagebox.showerror`.
- `_on_help_getting_started` — opens `docs/ONBOARDING.md` in
  `gui.doc_viewer.DocViewerDialog`. Fallback chain on viewer
  failure: `_open_in_default_app` → re-display first-run banner
  → one-paragraph messagebox.
- `_on_help_chartstack_guide` — opens `docs/chartstack.md` in the
  in-app doc viewer. Same fallback chain (last resort: messagebox
  naming Settings path + the `Ctrl+\`` toggle).
- `_on_help_custom_indicators_guide` — opens
  `docs/CUSTOM_INDICATORS.md` in the in-app doc viewer. Same
  fallback chain.
- `_on_help_entries_exits_guide` — opens `docs/ENTRIES_EXITS.md`
  in the in-app doc viewer. Same fallback chain (last resort:
  messagebox pointing at the Entries and Exits tabs).
- `_on_help_strategy_tester_guide` — opens
  `docs/STRATEGY_TESTER.md` in the in-app doc viewer. Same
  fallback chain (last resort: messagebox pointing at the
  Strategy tab).
- `_on_help_keyboard_shortcuts` — modeless Toplevel listing
  hotkeys grouped by feature in a `ttk.Treeview`. Singleton-per-app
  (re-invoke lifts existing). ESC and Close teardown + destroy.
  Content from module-level `_keyboard_shortcut_groups()` so tests
  can pin shape without Tk.
- `_on_help_documentation_library` — opens `DocViewerDialog` with
  no specific doc pre-selected; sidebar lists every bundled `.md`.
- `_on_help_reveal_data_folder` — opens data folder in OS file
  manager: `os.startfile` (Win) / `open` (macOS) / `xdg-open`
  (Linux). Falls back to path-display dialog.
- `_on_help_configure_credentials` —
  `gui.credentials_dialog.open_credentials_dialog(self)`.
- `_on_help_check_for_updates` — fires
  `updates.schedule_check_async(self.after, _present, force=True)`
  and presents the `UpdateResult` as a messagebox.
- `_on_help_reset_install` — confirm-then-`shutil.rmtree` the data
  folder, then exit via `self._on_close()`.

## Wiring

1. `ChartApp` class bases include `HelpMenuMixin`.
2. `_build_menubar` calls `self._build_help_menu(menubar)` and
   appends to `self._menubar_submenus` so theme repaint also
   styles the cascade.

## Alt+H mnemonic disabled

The cascade is added with `menubar.add_cascade(label="Help",
menu=m, underline=-1)`. The explicit `underline=-1` suppresses
Tk's default first-letter Alt mnemonic on Windows, freeing the
Alt+H keystroke for `_on_alt_h_placement` (TradingView-style
horizontal-line placement — see `app.spec.md` §Horizontal-line
drawings). Without this, pressing Alt+H opened the Help menu and
highlighted "About TradingLab", silently swallowing the drawing
hotkey users expected.

## Related

`_on_help_configure_credentials` and `_on_help_reveal_data_folder`
handlers are still defined here but are called from the Tools
cascade's `command=` hand-offs (the entries themselves moved to
Tools).
