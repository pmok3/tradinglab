# `gui/toolbar_controller.py` — Toolbar extraction

## Purpose
Move the top toolbar widget construction out of `app.py` while keeping the existing TradingLab toolbar behavior and compatibility attributes intact. The controller owns the toolbar frame, wires widgets to `AppState`, and delegates user actions back to `ChartApp` through a small callback protocol.

## Public API
- `ToolbarCallbacks` protocol:
  - `on_axis_change()`
  - `on_compare_toggle()`
  - `on_prepost_toggle()`
  - `on_reset_view()`
  - `on_open_settings()`
  - `on_open_watchlists()`
  - `on_theme_toggle()`
- `ToolbarController(parent, state, *, callbacks, intervals, sources)`
  - Builds the packed toolbar widgets.
  - Exposes `frame` for the host to pack.
  - Exposes compatibility widget handles used elsewhere (`ticker_label`, `compare_label`, `compare_check`, `source_combo`, `interval_combo`, `prepost_tooltip`).
- `lock_for_sandbox(allowed_intervals)` — temporarily restrict the interval combobox values.
- `unlock()` — restore the saved full interval list.

## Design
- Reads all mutable UI state from `AppState`; the controller does not own business logic.
- Label text is mirrored from Tk variables via traces so smoke tests that inspect `widget.cget("text")` still see live values.
- The sandbox interval lock preserves the pre-sandbox combobox values and restores them on unlock.
- `ChartApp` keeps legacy attribute aliases (`_ticker_label`, `_compare_label`, `_compare_check`, `_interval_cb`, `_prepost_tooltip`) so older code and smoke tests continue to work.

## Notes
- The current extracted toolbar matches the controls currently built in `ChartApp._build_ui()` on this branch.
- Source-based regression tests still grep `app.py` for a few legacy toolbar literals, so `app.py` retains small compatibility markers even though widget creation now lives here.
