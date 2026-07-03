# indicators/prior_day.py — Spec

## Purpose
Prior Day High / Low / Close (PDH / PDL / PDC) reference lines for intraday charts. Draws three horizontal lines at the previous completed regular-session trading day's high, low, and close. These are the most fundamental S/R levels for discretionary intraday trading: PDH/PDL define yesterday's range (breakout/rejection setups), PDC defines the gap (above/below yesterday's close).

## Public API
- `class PriorDayHLC` — overlay indicator, `kind_id = "prior_day_hlc"`, `kind_version = 2`.
  - `compute_arr(bars: Bars) -> {"prior_day_high": ndarray, "prior_day_low": ndarray, "prior_day_close": ndarray}`
  - `is_available_for(interval) -> Availability` — intraday only (auto-hides on 1d/1wk/1mo).
  - `params_schema` — three boolean toggles: `show_high` (default ON), `show_low` (default ON), `show_close` (default ON). Each independently enables/disables its output line.
  - **Canonical** output keys are spelled out: `prior_day_high`, `prior_day_low`, `prior_day_close` (the persisted style / per-output-visibility keys).
  - `effective_output_keys(params) -> tuple[str, ...]` — classmethod override. Returns ONLY the enabled levels' keys (`show_high`/`show_low`/`show_close`). A deselected level (e.g. `show_close=False`) is dropped from the **effective** output set so it does NOT appear on the chart — neither as a readout-legend entry nor in the per-output bookkeeping. `compute_arr` still returns the full three-key dict (disabled levels all-NaN) for back-compat with the persisted style/visibility keys and the output-key tests; `effective_output_keys` is the single source of truth for which outputs are shown. Missing `show_*` keys default to ON (back-compat).
  - `legend_label(display_name, params) -> str` — classmethod override. Returns the clean display name (e.g. `"Prior Day H/L/C"`) verbatim, suppressing the generic `format_indicator_label` params walker so the chart legend does NOT show the `(True, show_low=True, show_close=True)` boolean-toggle suffix.
  - `output_key_label(key) -> str` — classmethod override. Maps the verbose canonical keys to compact **display** labels for the in-chart readout legend: `prior_day_high → pd_high`, `prior_day_low → pd_low`, `prior_day_close → pd_close`. Persisted keys are unchanged; only the band label beside each value is shortened.

## Dependencies
- Internal: `indicators.sessions.session_groups_np`, `indicators.sessions.is_intraday_np`, `indicators.base.intraday_only`, `core.bars.Bars`.
- External: `numpy`.

## Design Decisions
- **Regular session only.** PDH/PDL/PDC are computed from 9:30–16:00 ET bars only (`regular_only=True`). Extended hours are excluded — low-liquidity pre/post noise doesn't define the institutional-accepted prior day range.
- **Derive from intraday bars, not daily bars.** Uses `session_groups_np` to group loaded intraday bars by calendar day and compute H/L/C from the prior day's group. No coupling to the data fetching layer — the indicator stays pure. Trade-off: if only today's bars are loaded (no prior day in the data), all outputs are NaN and no lines appear.
- **Constant per session with seam breaks.** Bars in a given intraday session carry the same PDH/PDL/PDC values, except the last regular bar of each already-referenced session is reset to NaN before the next session starts. That NaN break prevents matplotlib from drawing a vertical connector when the level changes day-to-day.
- **Rolling per day.** When multiple days are loaded, each day's bars use the immediately preceding day's H/L/C. Day 3's bars show day 2's levels, not day 1's.
- **PDC = last regular bar's close.** Not the session VWAP or a weighted close — the literal closing price of the last regular-session bar.
- **Boolean toggles for each level.** Three `bool` params (`show_high`, `show_low`, `show_close`, all default ON) let users enable/disable each line independently via checkboxes in the Manage Indicators dialog. When a toggle is OFF: (1) `compute_arr` leaves the corresponding output array all-NaN (no line drawn), AND (2) `effective_output_keys` drops that key so the deselected level does NOT appear in the in-chart readout legend either. Both halves are needed — the all-NaN array alone hides the *line* but a stale `effective_output_keys` would keep showing the level's legend entry (the bug fixed in the `prior-day-effective-keys` change). `effective_output_keys` is the single source of truth for what is shown.
- **Output keys spelled out on disk; abbreviated in the UI.** The canonical / persisted keys are `prior_day_high`, `prior_day_low`, `prior_day_close` — kept stable so `style` colour overrides and per-output visibility flags round-trip. For display, the `output_key_label` override shortens them to `pd_high` / `pd_low` / `pd_close` in BOTH the in-chart readout legend (`gui/readout_legend.py:_key_label_for`) and the per-output colour-swatch labels in the Manage Indicators dialog (`gui/indicator_dialog.py:_rebuild_color_buttons`). The swatch is still keyed by the canonical key — only the label text is shortened.
- **Legend prefix shows the clean name only.** `legend_label` overrides the generic `format_indicator_label` params walker (which would otherwise render the three boolean toggles as a noisy `(True, show_low=True, show_close=True)` suffix). The active levels are already conveyed by the display name (`Prior Day H/L/C` → `Prior Day H/L`) and the per-output `pd_*` labels. Mirrors AVWAP's `legend_label` override (audit `avwap-anchor-only-label`).
- **PDH/PDL default colours follow the bull/bear palette.** `default_style` sets `prior_day_high` to `constants.BULL_COLOR` and `prior_day_low` to `constants.BEAR_COLOR` (PDC stays neutral grey `#9e9e9e`) — PDH reads as support-from-above / bullish reclaim, PDL as breakdown / bearish, matching candle hues. Sourced from the constants so they follow the Okabe-Ito color-blind palette (orange/blue) on a fresh launch / indicator-add rather than being hardcoded teal/salmon. Audit `color-blind-palette-audit`.

## Invariants
1. Output arrays are always the same length as input bars.
2. First session's bars are always NaN (no prior day to reference).
3. All bars within a session carry identical PDH/PDL/PDC values, except deliberate NaN seam breaks at prior-session tails once a later session exists.
4. Only regular-session bars contribute to the prior day's H/L/C.
5. Auto-hidden on non-intraday intervals via `is_available_for`.
6. `effective_output_keys(params)` contains a level's key **iff** its `show_*` toggle is on; a deselected level is absent from the effective set (no line, no legend entry). Pinned by `tests/unit/test_prior_day_hlc.py::TestEffectiveOutputKeys` and the cross-indicator meta-test `tests/unit/indicators/test_indicator_schema_invariants.py::test_effective_output_keys_are_backed_by_finite_compute_values`.

## Testing
- `tests/unit/test_prior_day_hlc.py` — covers basic two-day computation, three-day rolling, single-day all-NaN, empty input, daily bars all-NaN, constant-within-session/seam-break behaviour, extended hours excluded, availability gating, kind_id, overlay flag, and output key consistency.

