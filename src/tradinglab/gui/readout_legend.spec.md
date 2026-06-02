# `gui/readout_legend.py` — pure enumeration for the in-readout overlay legend

## Purpose

Computes the **rows** for the TradingView-style overlay-indicator legend
that lives *inside* the top-left matplotlib readout offsetbox (replacing
the retired opaque Tk `OverlayLegend` pill — see
`gui/overlay_legend.spec.md`).

As of the **`legend-condensation`** sprint:

- **One row per indicator config**, not one per output. Multi-output
  indicators (Bollinger, AVWAP-with-bands, …) collapse to a single
  consolidated row of the form
  ``IndicatorName(params) upper <v1> middle <v2> lower <v3>`` with
  each band's value in its own colour. The renderer in
  `gui.interaction.InteractionMixin._build_readout_indicator_rows`
  packs the row as an `HPacker` of `TextArea`s so the colour-per-token
  story works.
- **Per-output visibility honoured.** The indicator class declares its
  visible output set via `effective_output_keys(params)` (e.g. AVWAP
  with `bands="off"` returns only `("avwap",)`); the per-output
  `LineStyle.visible` flag on the config further filters the row.

Everything here is a **pure function** of the `IndicatorManager` +
theme — no Tk, no matplotlib — so it is unit-testable headless.

## Public API

- `OverlaySegment` (frozen dataclass): one output of a multi-output
  indicator inside an overlay legend row.
  - `output_key: str` — identifies the line for live-value reads.
  - `key_label: str` — band name shown beside the value
    (`"upper"`, `"middle"`, `"lower"`). Empty for single-output
    indicators where the parenthesised label already disambiguates.
  - `color: str` — resolved colour for this output's value text.

- `ReadoutLegendRow` (frozen dataclass):
  - `config_id: int` — identifies the indicator config for routing
    right-click / double-click gestures back to the per-indicator
    dialog + context menu.
  - `label: str` — row prefix text, typically
    `"IndicatorName(param1, name2=val2, ...)"` from
    `format_indicator_label`. Rendered in the theme's neutral text
    colour.
  - `outputs: list[OverlaySegment]` — visible output segments in
    indicator-declared order (top-down on chart).
  - `visible: bool` — mirrors `cfg.visible`; hidden rows are greyed.

- `build_overlay_legend_rows(manager, scope, interval, *,
  theme_text="#cccccc") -> list[ReadoutLegendRow]`:
  - Enumerates via `overlay_legend.collect_overlay_configs` (manager
    insertion order), **including hidden configs** (re-enable-able).
  - For each config, calls the indicator class's
    `effective_output_keys(params)` to get the visible output set
    (declares which bands are actually rendered for these params),
    then filters by per-output `cfg.style[key].visible` (user toggle).
  - Returns `[]` on any failure (fail-safe — legend simply absent).

- `format_indicator_label(cfg: IndicatorConfig) -> str`:
  - Builds the `"DisplayName(param1, name2=val2, ...)"` prefix.
  - If `display_name` already contains a parenthesised suffix (the
    factory convention `self.name = "SMA(20)"`), returns it as-is so
    we don't double up.
  - Otherwise walks the indicator factory's `params_schema` in
    declaration order: first non-empty param positional
    (`typical`), remaining params `name=value` (`bands=off`).
    Empty / missing params are skipped.
  - Empty `display_name` + unknown kind_id → bare `display_name`.
    Empty `display_name` + known kind_id → uppercased kind_id
    (`"sma"` → `"SMA"`) so the legend matches the registry's display
    labels.

## Resolution rules

- **Visible output keys** (`_effective_output_keys_for`):
  - indicator factory's `effective_output_keys(params)` → the
    canonical visible set (e.g. AVWAP returns `("upper2", "upper1",
    "avwap", "lower1", "lower2")` when `bands="both"`);
  - filtered by config per-key `style[key].visible` (user toggle).
- **Colour** (`_color_for_key`): config per-key `style[key].color`
  → factory `default_style[key].color` → `theme_text` (neutral).
- **Label**: `format_indicator_label(cfg)`. The per-output band name
  is on the `OverlaySegment.key_label`; the renderer concatenates
  them with the row label.

## Design decisions

- **Hidden configs included.** Matches the retired pill's affordance:
  a hidden overlay stays in the legend (greyed) so right-click → Show
  re-enables it. The renderer sets `line=None` for every output
  segment of a hidden row (no live value, just the greyed name).
- **Pure / Tk-free.** Keeps the testable core isolated from the
  matplotlib + hit-test machinery in `interaction.py`.
- **Reuses `collect_overlay_configs`.** Single source of truth for
  "which configs are overlay-class on this `(scope, interval)`" — so
  the legend and the renderer never disagree about membership.
- **Indicator declares output order.** `effective_output_keys` returns
  outputs in canonical visual top-down order (Bollinger ships
  `("upper", "middle", "lower")`, AVWAP ships `("upper2", "upper1",
  "avwap", "lower1", "lower2")`). New multi-output indicators that
  want a non-default order MUST override.

## Tests

- `tests/unit/gui/test_readout_legend.py` — pre-existing test file
  updated for the new shape: rows carry segments (each with their
  own colour), multi-output indicators collapse to one row.
- `tests/unit/gui/test_readout_legend_condensation.py` — new (20
  tests) pinning `effective_output_keys` for AVWAP/BB/EMA/SMA,
  `format_indicator_label` formatting rules, per-output visibility
  filtering, hidden-config-still-emitted invariant.
