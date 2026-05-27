# gui/_widget_metrics.py — Spec

## Purpose

Single source of truth for the Tk font + widget pixel metrics used by the
fit-based layout classifiers across the GUI. Originally duplicated inline in
`gui/scanner_block_editor.py` (CLAUDE.md §7.19) and `gui/indicator_dialog.py`
(`_compute_max_cols_for_schema`); consolidated here so future tuning happens
in one place.

Audit #9 in `files/generalization-audit.md`: the original Windows-Segoe-UI-9pt
hardcoded constants under/over-estimated on macOS (San Francisco) and Linux
(DejaVu Sans). Values are now measured at runtime against the active named
Tk font and cached per-font-name, invalidated on every theme apply.

## Public API

### `metrics_for(font_name: str = "TkDefaultFont") -> dict[str, int]`

Returns a dict with four positive-integer keys measured against the named
Tk font:

| Key                | Source                                                    |
|--------------------|-----------------------------------------------------------|
| `char_px`          | `round(f.measure("abcdef…0-9") / 36)` (average glyph)     |
| `combo_overhead`   | `max(25, int(linespace * 1.9))` (Combobox chrome)         |
| `spinbox_overhead` | `max(20, int(linespace * 1.5))` (Spinbox chrome)          |
| `entry_overhead`   | `max(12, int(linespace * 0.9))` (Entry chrome)            |

Returns the SAME dict instance on repeat calls for the same `font_name` —
cached in module-level `_METRICS_CACHE`.

Falls back to the Windows-Segoe-UI-9pt constants (7 / 25 / 20 / 12) when
Tk isn't initialised (no default root, font missing, zero-width
measurement) so module-level imports during test discovery don't crash.

### `invalidate_metrics_cache() -> None`

Drops `_METRICS_CACHE` entirely. Call after a theme/font change so the
next `metrics_for(...)` re-measures.

**Wired into `ThemeController.apply`** — fired immediately after the
theme dict is committed, before any window / ttk-style repaint, so
consumers' next read picks up the new font measurements.

### Back-compat module-level constants

| Name                 | Type             | Notes                                                                |
|----------------------|------------------|----------------------------------------------------------------------|
| `_CHAR_PX`           | `_ConstantProxy` | Defers to `metrics_for()["char_px"]` at read time.                   |
| `_COMBO_OVERHEAD`    | `_ConstantProxy` | Defers to `metrics_for()["combo_overhead"]`.                         |
| `_SPINBOX_OVERHEAD`  | `_ConstantProxy` | Defers to `metrics_for()["spinbox_overhead"]`.                       |
| `_ENTRY_OVERHEAD`    | `_ConstantProxy` | Defers to `metrics_for()["entry_overhead"]`.                         |
| `_CHECKBOX_PX`       | `int = 22`       | Plain int — not font-derived (caller adds a font-measured label).    |
| `_FRAME_PAD_PX`      | `int = 6`        | Plain int — pure layout constant, not font-dependent.                |

`_ConstantProxy` overrides `__int__`, `__index__`, `__float__`, plus the
arithmetic + comparison + hash dunders so it transparently substitutes
for an `int` in every existing call site (`_CHAR_PX * N`, `n_widgets *
_FRAME_PAD_PX + _COMBO_OVERHEAD`, `int(widest_chars * _CHAR_PX)`, etc.)
without source changes in `scanner_block_editor.py` / `indicator_dialog.py`.

## Dependencies

- `tkinter` + `tkinter.font` for `Font.measure` / `Font.metrics`.
- No third-party deps. Cache + proxy are pure-python.

## Design decisions

- **Lazy measurement.** Constructing a `tkfont.Font` requires a default
  root. Many call sites (test discovery, module-level imports of
  `scanner_block_editor`) happen before any Tk root exists. Measurement
  is deferred to first `metrics_for` call; the cached fallback dict is
  returned on the no-root path.
- **Average glyph, not max.** Historical `_CHAR_PX = 7` was a
  Segoe-UI-9pt *average* per char, and callers do `N * _CHAR_PX`
  against arbitrary strings — average is the right model.
  `f.measure("M")` would over-estimate by ~50% (M is the widest
  narrow-latin glyph) and falsely flip many simple conditions into
  the stacked layout.
- **Same proxy instance returned every read.** Cache is keyed by
  `font_name` so a theme apply that swaps `TkDefaultFont`'s underlying
  config (size / family) is picked up only after
  `invalidate_metrics_cache` runs — exactly what
  `ThemeController.apply` guarantees.
- **Per-key fallback floor.** `max(fallback, int(linespace * mult))`
  guarantees the measured value never drops below the documented
  Windows baseline. A tiny font (linespace = 8) won't break the auto-
  stack classifier; the worst case is a slightly more aggressive
  stacked layout, which is the safe direction.
- **`_CHECKBOX_PX` stays plain.** Its existing call site already adds
  a separately font-measured label width on top, so deriving the
  indicator pixel count from `linespace` would double-count.
- **`_FRAME_PAD_PX` stays plain.** Pure ttk grid `padx` constant — has
  no font dependency.
- **Proxy not a `NewType(int)` subclass.** A direct `int` subclass
  would freeze the value at construction time, defeating the
  on-theme-change re-read. The proxy reads through `_value()` on
  every dunder, so each arithmetic / comparison op sees the current
  measurement.

## Invariants

- All four `metrics_for` values are positive integers.
- `metrics_for(name)` returns the same dict instance on consecutive
  calls for the same `name` until `invalidate_metrics_cache()` is
  called.
- `int(_CHAR_PX) == metrics_for()["char_px"]` at every read.
- The module performs no side effects on import (no measurement
  happens until first `metrics_for` call).
- `_ConstantProxy` instances are NOT singletons across module reloads;
  callers must import via `from ._widget_metrics import _CHAR_PX`
  (already the established pattern).
