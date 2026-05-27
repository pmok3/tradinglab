# gui/_widget_metrics.py — Spec

## Purpose

Single source of truth for the empirically-calibrated Tk font + widget pixel
metrics used by the fit-based layout classifiers across the GUI. Originally
duplicated inline in `gui/scanner_block_editor.py` (CLAUDE.md §7.19) and
`gui/indicator_dialog.py` (`_compute_max_cols_for_schema`); consolidated here
so future tuning happens in one place.

## Public API

Module-level constants (no functions or classes):

| Name                 | Value | Meaning                                                                |
|----------------------|------:|------------------------------------------------------------------------|
| `_CHAR_PX`           | `7`   | Pixels per character (ttk default Segoe UI 9pt on Windows).            |
| `_COMBO_OVERHEAD`    | `25`  | `ttk.Combobox` border + dropdown arrow overhead.                       |
| `_SPINBOX_OVERHEAD`  | `20`  | `ttk.Spinbox` border + up/down arrow overhead.                         |
| `_CHECKBOX_PX`       | `22`  | `ttk.Checkbutton` indicator + small inline label overhead.             |
| `_ENTRY_OVERHEAD`    | `12`  | `ttk.Entry` border overhead.                                           |
| `_FRAME_PAD_PX`      | `6`   | Default per-gap horizontal padding allowance between widgets in a row. |

All names are intentionally underscore-prefixed: they are an internal
package convention, re-exported into individual GUI modules via direct
import, not part of the package's stable public API.

## Dependencies

None — pure module-level constants. No Tk import, no third-party deps.

## Design decisions

- **Same constants everywhere.** Both classifiers (`_ConditionFrame` and
  `IndicatorDialog`'s param-grid) need to estimate widget widths from
  `ParamDef` shapes. Calibrating these independently would drift over time;
  any future font / theme tweak should be a one-file change.
- **Ballpark, not exact.** The integer values are only good to ±20 px on
  Windows and may be a couple of pixels off on macOS / Linux. Both callers
  apply their own stability buffer:
  * `_ConditionFrame._classify_layout` has a `_HYSTERESIS_PX = 80` buffer
    around the inline ↔ stacked flip boundary.
  * `_compute_max_cols_for_schema` returns an integer column count from
    `avail_px // widest_px`; the integer floor naturally suppresses
    sub-`widest_px` width changes from causing a flip.
- **Underscore-prefixed names.** Match the existing `_CHAR_PX` etc.
  convention from `scanner_block_editor.py` so the re-exports are
  drop-in compatible with existing tests that import from there.

## Invariants

- Values are positive integers. None of the callers handle non-int
  metrics.
- The module performs no side effects on import.
