# gui/indicator_menu

## Purpose

Hosts the menu-callback handlers for the **Indicators** cascade in the
menubar built by `ChartApp._build_menubar` — Add (per kind/scope),
Clear, Save Preset, Load Preset cascade, Delete Preset cascade.

Extracted from `app.py` to keep `ChartApp` focused on chart-state and
data-pipeline concerns. Pure UI glue: each method validates input,
mutates `self._indicator_manager`, and reports outcome via `self._status`.

## Public Surface

- `class IndicatorMenuMixin`. All methods are private (leading underscore)
  and called either by menu commands wired in `ChartApp._build_menubar`
  or by other ChartApp methods (e.g., the Add Indicator dialog calls
  `_on_menu_add_indicator` after collecting params from the user):
  - `_on_menu_add_indicator(kind_id, params, scopes=None)`
  - `_on_menu_clear_indicators()`
  - `_populate_indicator_preset_menu(menu, action)` — `postcommand`
    handler that rebuilds Load/Delete cascades when opened so the
    list always reflects the live `IndicatorManager.list_presets()`.
  - `_on_menu_save_indicator_preset()`
  - `_on_menu_load_indicator_preset(name)`
  - `_on_menu_delete_indicator_preset(name)`

## Mixin Rules

- No `__init__`.
- No cooperative `super()` — method resolution relies on plain MRO
  through `ChartApp(InteractionMixin, WatchlistTabMixin,
  WorkerPoolMixin, IndicatorMenuMixin, SandboxMenuMixin, tk.Tk)`.
- No name collisions with other mixins or `ChartApp`.

## Required Instance State (provided by ChartApp)

- `self._indicator_manager` — `indicators.config.IndicatorManager`.
- `self._status` — status-bar facade (`info`/`warn`/`error` methods).
- `self._theme` — current theme dict; passed to
  `self._apply_menubar_theme` after rebuilding cascades so freshly-
  added entries inherit the active theme colours.

## Notes

- `_populate_indicator_preset_menu` reapplies the menubar theme after
  `delete + add_command` because the per-entry `activebackground` /
  `foreground` only sticks after a repaint.
- Theme fallback: if `self._theme` isn't set yet (extremely-early
  startup), defaults to `LIGHT_THEME` from `tradinglab.constants`.
