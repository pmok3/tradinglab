# gui/indicator_menu

## Purpose

Hosts the menu-callback handlers for the **Indicators** cascade in the
menubar built by `MenuBuilder` — Clear, Save Preset, Load Preset
cascade, Delete Preset cascade, Custom Indicator Builder, plus
file-based Save/Load Preset to/from File (Save-As / portable copies).
It also keeps the legacy/test quick-add helper.

Extracted from `app.py` to keep `ChartApp` focused on chart-state and
data-pipeline concerns. Pure UI glue: each method validates input,
mutates `self._indicator_manager`, and reports outcome via `self._status`.

## Public Surface

- `class IndicatorMenuMixin`. All methods are private (leading underscore)
  and called either by menu commands wired in `MenuBuilder`
  or by tests / legacy quick-add callers:
  - `_on_menu_add_indicator(kind_id, params, scopes=None)`
  - `_on_menu_clear_indicators()`
  - `_populate_indicator_preset_menu(menu, action)` — `postcommand`
    handler for Load/Delete cascades. Caches by `(action,
    tuple(names), active)` on the `Menu` widget and skips rebuilding
    when neither the preset list nor active preset changed; any
    save/load/delete mutates that key so the next open rebuilds.
  - `_on_menu_save_indicator_preset()`
  - `_on_menu_load_indicator_preset(name)`
  - `_on_menu_delete_indicator_preset(name)`
  - `_on_menu_save_indicator_preset_to_file()` — **Save-As** export of the
    live active indicator set to a user-chosen `.json` path via
    `filedialog.asksaveasfilename`, delegating to
    `indicators.preset_store.export_preset_to_file`. Empty active set →
    status warning, no dialog. Cancelled dialog → no-op. A write failure
    surfaces a `messagebox.showerror`. Audit `indicator-save-location`.
  - `_on_menu_load_indicator_preset_from_file()` — **Load** an indicator
    preset from a user-chosen `.json` via `filedialog.askopenfilename` +
    `indicators.preset_store.import_preset_from_file`. REPLACES the live
    active set (`manager.clear()` then `manager.add(IndicatorConfig.from_dict(d))`
    per entry; malformed entries skipped). Unreadable / malformed / wrong
    shape → `messagebox.showerror`; cancelled → no-op.
  - **File-based vs name-based presets.** The two file handlers above are a
    Save-As / portable-copy path, **independent** of the name-keyed
    auto-persist envelope. They emit `clear` / `add` manager events (not
    `preset_saved` / `preset_loaded`), so they do NOT touch the
    `indicators.preset_store` envelope or the active-preset pointer.
  - **Auto-persist side effect:** the name-based handlers
    (`_on_menu_save_indicator_preset` / `_on_menu_load_indicator_preset` /
    `_on_menu_delete_indicator_preset`) call `IndicatorManager.save_preset`
    / `set_preset` / `delete_preset`, which fire `preset_saved` /
    `preset_loaded` / `preset_deleted`. `ChartApp._on_indicator_preset_persist`
    (a separate manager subscriber) writes those changes to the standalone
    `indicators.preset_store` file, so a saved preset survives an app restart
    (restored on launch via `install_presets`) WITHOUT a File → Save
    Configuration. The menu handlers themselves do no I/O — persistence is
    purely a manager-event side effect.
  - `_on_custom_indicator_builder()` — opens the
    `gui.custom_indicator_dialog.CustomIndicatorDialog` via
    `open_custom_indicator_dialog(self)`. The dialog itself is
    safe to open regardless of `custom_indicators_enabled`; the
    handler reads that setting only to surface a status warning
    when off so the user knows saved files won't auto-load on
    next startup.

## Mixin Rules

- No `__init__`.
- No cooperative `super()` — method resolution relies on plain MRO
  through `ChartApp`'s mixin block (with `tk.Tk` last).
- No name collisions with other mixins or `ChartApp`.

## Required Instance State (provided by ChartApp)

- `self._indicator_manager` — `indicators.config.IndicatorManager`.
- `self._status` — status-bar facade (`info`/`warn`/`error` methods).
- `self._theme` — current theme dict; passed to
  `self._apply_menubar_theme` after rebuilding cascades so freshly-
  added entries inherit the active theme colours.

## Notes

- `_populate_indicator_preset_menu` reapplies the menubar theme after
  an actual `delete + add_command` rebuild because the per-entry
  `activebackground` / `foreground` only sticks after a repaint.
- Theme fallback: if `self._theme` isn't set yet (extremely-early
  startup), defaults to `LIGHT_THEME` from `tradinglab.constants`.
