# `gui/indicator_acronyms.py`

## Purpose
Single source of truth for the per-indicator tooltip blurbs surfaced by the Manage Indicators dialog and the per-indicator popup. Users new to technical analysis (or to a niche indicator like LRSI / RRVOL) see a fog of acronyms with no explanation; this module supplies a one-line full-name + brief-description blurb keyed by `IndicatorFactory.kind_id`.

## Public surface
- `ACRONYMS: dict[str, tuple[str, str]]` — `kind_id → (full_name, brief_blurb)`. Currently covers `ma`, `sma`, `ema`, `vwap`, `avwap`, `rsi`, `lrsi`, `macd`, `smi`, `adx`, `atr`, `rvol`, `rrvol`, `bbands`, `bbands_ema`, `keltner`, `chandelier`, `prior_day_hlc`, `overlap_score_inv`.
- `explain_kind_id(kind_id: str) -> str` — returns a two-line tooltip blurb (`"Full Name\nbrief blurb"`). Unknown `kind_id` degrades gracefully to the raw id so third-party / user-plugin indicators that haven't been documented here still show something.

## Format invariants
- Keyed by `kind_id` (not display name) because `IndicatorFactory.kind_id` is the stable persistence key; `IndicatorConfig.display_name` can be renamed by the user.
- Blurb ≤ ~80 chars so it fits a single-line ToolTip without ugly wrapping.
- First line = full name, second line = blurb. The newline separator is consumed by the tooltip widget's wrap logic.

## Maintenance
Adding a new built-in indicator to the registry SHOULD include an entry here, but it's a soft requirement — `explain_kind_id` falls back to the bare id, so the lookup never raises. The Manage Indicators dialog renders the raw id in that case (acceptable for power-user plugins that don't ship with docs).

## Consumers
- `gui.indicator_dialog._IndicatorDialog` — kind-combobox tooltip
- `gui.per_indicator_dialog._PerIndicatorDialog` — per-row header tooltip

## Tests
No dedicated unit suite; the function is pure-data-lookup with one-line fallback. Coverage comes from smoke tests that open the Manage Indicators dialog.
