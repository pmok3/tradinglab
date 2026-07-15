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
  - Exposes compatibility widget handles used elsewhere (`ticker_label`, `compare_label`, `compare_check`, `source_combo`, `interval_combo`, `prepost_check`, `prepost_tooltip`). The **`ticker_label` / `compare_label`** read-only displays are `width=14` so a ratio symbol like `AMD/NVDA` fits without truncation. **`compare_check` is a toggle `ttk.Button`** (not a `ttk.Checkbutton`) — see Design.
  - `sources` is the user-visible source list (callers MUST pass `data.user_visible_sources()`, NOT `data.DATA_SOURCES.keys()`, so internal-only sources like `synthetic` / `synthetic-stream` are filtered out of the combobox — see `data/base.spec.md`). The combobox shows the raw source keys verbatim (no display-name layer); built-ins appear in registration order (`yfinance`, `Auto`, then credential-gated vendors such as `alpaca` and `yfinance+alpaca`).
- `lock_for_sandbox(allowed_intervals)` — temporarily restrict the interval combobox values.
- `unlock()` — restore the saved full interval list.
- `set_sources(sources)` — replace the source combobox values after
  BYOD source registration changes.
- `interval_saved_values` — read-only snapshot of the interval values
  saved by the sandbox lock, or `None` when unlocked.

## Design
- Reads all mutable UI state from `AppState`; the controller does not own business logic.
- Label text is mirrored from Tk variables via traces so smoke tests that inspect `widget.cget("text")` still see live values.
- **Compare on/off is a fixed-width toggle button, not a checkbox** (audit `compare-toggle-button`). Layout is `Compare:` label → `compare_check` button → `compare_label` (the compare-ticker display). `compare_check` is a `ttk.Button` (`width=5`, themed `TButton` — `Toolbutton` is intentionally avoided because it isn't in `build_ttk_style_spec` and would render unthemed in dark mode) whose text is just the state `On` / `Off` (the `Compare:` prefix is the static label to its left). Its `command` (`_on_compare_button`) flips the `compare` BooleanVar (still the source of truth read by `_render` / `_on_compare_toggle` / topology key everywhere) and then calls `on_compare_toggle` — mirroring a checkbox's flip-then-command order. A trace (`_bind_compare_button`) keeps the `On`/`Off` text in sync whether toggled by the button or set programmatically (failed-compare revert, sandbox replay). The attribute name `compare_check` is retained for back-compat even though the widget is now a button.
- The sandbox interval lock preserves the pre-sandbox combobox values and restores them on unlock.
- `ChartApp` keeps legacy attribute aliases (`_ticker_label`, `_compare_label`, `_compare_check`, `_interval_cb`, `_prepost_tooltip`) so older code and smoke tests continue to work.

## Notes
- The current extracted toolbar matches the controls currently built in `ChartApp._build_ui()` on this branch.
- Source-based regression tests still grep `app.py` for a few legacy toolbar literals, so `app.py` retains small compatibility markers even though widget creation now lives here.
