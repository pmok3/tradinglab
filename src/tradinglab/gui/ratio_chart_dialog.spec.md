# `gui/ratio_chart_dialog.py` — Ratio chart composer

## Purpose
Discoverable Tools-menu entry for the existing ratio pseudo-symbol feature.
Users can chart ratios such as `AMD/NVDA` without remembering the typed ticker
syntax.

## Public API
- `RatioChartDialog(parent, *, on_submit)` — `BaseModalDialog`; Return =
  Chart Ratio, ESC = Cancel. Pure ttk widgets (no classic Tk widgets → no
  `native_theme` helper needed).
- `open_ratio_chart_dialog(parent, *, on_submit) -> RatioChartDialog | None`
  — construct + `wait_window`; `None` on `TclError`.

## Flow
1. The Presets combobox lists `RATIO_PRESETS` descriptions. Selecting one
   writes its numerator and denominator into the editable fields.
2. Numerator and denominator entries update a live preview using
   `ratio_display_label(f"{num}/{den}")`.
3. Chart Ratio validates both legs are non-empty plain ticker symbols and that
   `is_ratio_symbol(f"{num}/{den}")` accepts the result.
4. On success the dialog calls `on_submit(canonical_ratio_symbol(...))` and
   closes. Invalid input stays open and writes a status message.

## Dependencies
- Internal: `..data.{RATIO_DELIMITER, RATIO_PRESETS, canonical_ratio_symbol,
  is_ratio_symbol, ratio_display_label}`,
  `._modal_base.{BaseModalDialog, protect_combobox_wheel}`, `.colors`.
- Stdlib: `tkinter`, `collections.abc.Callable`.

## Wiring
`gui/menu_builder.py` adds **Tools → "New Ratio Chart…"** as the first Tools
item. `HelpMenuMixin._on_tools_new_ratio_chart` (`gui/help_menu.py`) opens the
dialog and submits via `_submit_ratio_chart`, which sets `ticker_var` and calls
`_schedule_reload(delay_ms=0)`.

## Design Decisions
- The dialog submits canonical `NUM/DEN` strings only; alias legs and nested
  ratios are rejected by the same `ratio_source` validator used by the chart
  loader.
- Presets are descriptions rather than raw symbols so the menu explains why a
  ratio is useful.
- The Presets combobox is protected with `protect_combobox_wheel` so wheel
  scrolling cannot silently change the selected preset.

## Invariants
- Confirming invalid input never closes the dialog or calls `on_submit`.
- Successful confirmation calls `on_submit` exactly once with a canonical
  uppercase, slash-delimited ratio symbol.
- The dialog remains pure ttk; if classic Tk widgets are added, add explicit
  dark-theme coverage.

## Testing
`tests/unit/gui/test_ratio_chart_dialog.py` covers preset population, valid
submission canonicalization, invalid empty/nested-leg validation, and status
message surfacing.
