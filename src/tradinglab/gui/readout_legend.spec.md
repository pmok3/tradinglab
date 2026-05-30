# `gui/readout_legend.py` — pure enumeration for the in-readout overlay legend

## Purpose

Computes the **rows** for the TradingView-style overlay-indicator legend
that lives *inside* the top-left matplotlib readout offsetbox (replacing
the retired opaque Tk `OverlayLegend` pill — see
`gui/overlay_legend.spec.md`). One row per overlay output (`SMA`, each
Bollinger band, …), rendered as a transparent `TextArea` under the OHLCV
strip so it never overlaps the readout and carries no opaque background.

Everything here is a **pure function** of the `IndicatorManager` + theme —
no Tk, no matplotlib — so it is unit-testable headless. The matplotlib
`TextArea` construction, live hover-value plumbing, and click hit-testing
live in `gui.interaction.InteractionMixin`
(`_build_readout_indicator_rows`, `_update_readout`,
`_maybe_handle_readout_legend_click` / `_readout_legend_row_hit`).

## Public API

- `ReadoutLegendRow` (frozen dataclass):
  - `config_id: int` / `output_key: str` — identify the line for value
    reads and for routing right-click / double-click gestures back to the
    per-indicator dialog + context menu.
  - `label: str` — display text: `display_name` for single-output
    indicators, `"display_name key"` for multi-output ones.
  - `color: str` — resolved swatch / text colour for the output.
  - `visible: bool` — mirrors `cfg.visible`; hidden rows are greyed by
    the renderer but kept so they can be re-enabled.
- `build_overlay_legend_rows(manager, scope, interval, *,
  theme_text="#cccccc") -> list[ReadoutLegendRow]`:
  - Enumerates via `overlay_legend.collect_overlay_configs` (manager
    insertion order), **including hidden configs** (re-enable-able).
  - Multi-output indicators expand in `default_style` key order.
  - Returns `[]` on any failure (fail-safe — legend simply absent).

## Resolution rules

- **Output keys** (`_output_keys_for`): factory `default_style.keys()` →
  config `style.keys()` → single synthetic key `kind_id` (so a styleless
  indicator still yields exactly one row).
- **Colour** (`_color_for_key`): config per-key `style[key].color` →
  factory `default_style[key].color` → `theme_text` (neutral).
- **Label**: single-output → `display_name`; multi-output →
  `"display_name key"` so the user can tell bands apart.

## Design decisions

- **Hidden configs included.** Matches the retired pill's affordance:
  a hidden overlay stays in the legend (greyed) so right-click → Show
  re-enables it. The renderer sets `line=None` for hidden rows (no live
  value, just the greyed name).
- **Pure / Tk-free.** Keeps the testable core isolated from the
  matplotlib + hit-test machinery in `interaction.py`.
- **Reuses `collect_overlay_configs`.** Single source of truth for
  "which configs are overlay-class on this `(scope, interval)`" — so the
  legend and the renderer never disagree about membership.
