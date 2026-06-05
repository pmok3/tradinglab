# `gui/chartstack_settings_dialog.py` — Spec

## Purpose
Small modal popup, reachable from `View → ChartStack Settings…`, that
edits the per-slot fixed-preset symbols persisted under
`chartstack.fixed_preset_symbols`. Each slot in the ChartStack panel
gets one `ttk.Entry` pre-populated with its current symbol; Save writes
the list back to settings and flips `chartstack.binding.mode` to
`"FIXED_PRESET"` so the user's picks become authoritative.

Audit tag: `chartstack-fixed-preset`.

## Public API
- `class ChartStackSettingsDialog(BaseModalDialog)` — the popup. Not
  normally constructed directly; callers go through
  `open_chartstack_settings`.
- `open_chartstack_settings(parent) -> ChartStackSettingsDialog` —
  the entry point. Called from `ChartApp._on_view_chartstack_settings`
  (View menu callback).
- `DEFAULT_PRESET: tuple[str, ...] = ("SPY", "QQQ", "VXX")` — the
  hardcoded reset baseline, mirrored verbatim from
  `gui.chartstack.settings_adapter.DEFAULTS["chartstack.fixed_preset_symbols"]`.

## Dependencies
- External: `tkinter`, `tkinter.ttk`.
- Internal: `tradinglab.settings` (for the persistence side),
  `._modal_base.BaseModalDialog` / `protect_combobox_wheel`,
  `.chartstack.settings_adapter` (for `card_count()` +
  `fixed_preset_symbols()`).

## Layout

```
+---- ChartStack Settings -----------------+
| Per-slot symbols for the ChartStack panel|
| Slot 1 sits at the top of the stack.     |
|                                          |
|   Slot 1: [ SPY      ]                   |
|   Slot 2: [ QQQ      ]                   |
|   Slot 3: [ VXX      ]                   |
|                                          |
| [Reset to Defaults]      [Save] [Cancel] |
+------------------------------------------+
```

Fixed size 340 × 260. Geometry persisted under `dlg.chartstack_settings`
(pinned by `tests/unit/gui/test_toplevel_geometry_wiring.py`).

## Behaviour

### Construction
- Reads `card_count()` and constructs that many entries.
- Pre-populates entries from `fixed_preset_symbols()` (already
  padded / truncated to `card_count`).

### Save
- Reads each entry's `.get()`, applies `.strip().upper()`.
- Writes the resulting list to `chartstack.fixed_preset_symbols`.
- Writes `"FIXED_PRESET"` to `chartstack.binding.mode` unconditionally
  so the user's picks immediately become authoritative even if they
  came in via HYBRID.
- If the parent (typically `ChartApp`) has a `_chartstack` attribute
  pointing at the live `ChartStackPanel`, calls `panel.refresh()` so
  the cards re-bind without waiting for the next event loop tick.
  Refresh failures are swallowed (never block Save).

### Cancel / Esc / WM_DELETE
- Leaves all settings untouched.

### Reset to Defaults
- Rewrites the entries' visible contents to `DEFAULT_PRESET`, padded
  with empty strings to `card_count`. Does NOT persist; user still
  needs to click Save.

## Design decisions

- **Modal `BaseModalDialog`, not modeless `Toplevel`.** Editing a
  preset is a "commit-or-cancel" operation; modal grab prevents the
  trader from clicking around in the chart and getting confused
  about whether their unsaved entry will land.
- **Reset rewrites entries, not settings.** Mirrors the Settings-style
  contract that nothing persists until Save. A trader who hits
  Reset then Cancel ends up exactly where they started.
- **`protect_combobox_wheel` is invoked** even though the dialog
  currently hosts only `ttk.Entry` widgets (no Combobox / Spinbox),
  per CLAUDE.md §7.11. Forward-compat for the inevitable day a
  binding-mode dropdown is added.
- **No validation beyond `.strip().upper()`.** Ticker syntax is
  free-form; the binding resolver tolerates blank slots (renders an
  empty card). Validating against an allow-list would force a
  yfinance round-trip and isn't worth the friction for a settings
  popup.
- **Save flips `binding.mode` to FIXED_PRESET.** The popup's UX is
  "I want exactly these symbols"; silently leaving the user on
  HYBRID would have them save the list and then watch the cards
  show something else (e.g. their open positions). The mode flip
  is the principle of least surprise.

## Testing
`tests/unit/gui/test_chartstack_settings_dialog.py` — covers:
construction with defaults, persisted-preset read, short/long
preset padding & truncation, save writes upper-cased entries,
save flips binding mode, save refreshes mounted `_chartstack` panel,
save tolerates absent panel, cancel leaves settings untouched,
reset rewrites entries in place (no settings write), public
`open_chartstack_settings` entrypoint.

## See also
- `gui.chartstack.binding` — `FIXED_PRESET` binding mode
  (slot-aligned resolution).
- `gui.chartstack.settings_adapter` — defaults table +
  `fixed_preset_symbols()` helper.
- `gui.menu_builder` — wires the `View → ChartStack → Settings…`
  cascade child to `ChartApp._on_view_chartstack_settings`.
