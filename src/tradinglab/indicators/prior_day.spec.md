# indicators/prior_day.py — Spec

## Purpose
Prior Day High / Low / Close (PDH / PDL / PDC) reference lines for intraday charts. Draws three horizontal lines at the previous completed regular-session trading day's high, low, and close. These are the most fundamental S/R levels for discretionary intraday trading: PDH/PDL define yesterday's range (breakout/rejection setups), PDC defines the gap (above/below yesterday's close).

## Public API
- `class PriorDayHLC` — overlay indicator, `kind_id = "prior_day_hlc"`, `kind_version = 2`.
  - `compute_arr(bars: Bars) -> {"prior_day_high": ndarray, "prior_day_low": ndarray, "prior_day_close": ndarray}`
  - `is_available_for(interval) -> Availability` — intraday only (auto-hides on 1d/1wk/1mo).
  - `params_schema` — three boolean toggles: `show_high` (default ON), `show_low` (default ON), `show_close` (default ON). Each independently enables/disables its output line.
  - Output keys are spelled out: `prior_day_high`, `prior_day_low`, `prior_day_close` (not abbreviated).

## Dependencies
- Internal: `indicators.sessions.session_groups_np`, `indicators.sessions.is_intraday_np`, `indicators.base.intraday_only`, `core.bars.Bars`.
- External: `numpy`.

## Design Decisions
- **Regular session only.** PDH/PDL/PDC are computed from 9:30–16:00 ET bars only (`regular_only=True`). Extended hours are excluded — low-liquidity pre/post noise doesn't define the institutional-accepted prior day range.
- **Derive from intraday bars, not daily bars.** Uses `session_groups_np` to group loaded intraday bars by calendar day and compute H/L/C from the prior day's group. No coupling to the data fetching layer — the indicator stays pure. Trade-off: if only today's bars are loaded (no prior day in the data), all outputs are NaN and no lines appear.
- **Constant per session.** All bars in a given intraday session carry the same PDH/PDL/PDC values (horizontal lines). The renderer draws these as flat horizontal lines spanning the visible range for that day.
- **Rolling per day.** When multiple days are loaded, each day's bars use the immediately preceding day's H/L/C. Day 3's bars show day 2's levels, not day 1's.
- **PDC = last regular bar's close.** Not the session VWAP or a weighted close — the literal closing price of the last regular-session bar.
- **Boolean toggles for each level.** Three `bool` params (`show_high`, `show_low`, `show_close`, all default ON) let users enable/disable each line independently via checkboxes in the Manage Indicators dialog. When a toggle is OFF, the corresponding output array stays all-NaN (no line drawn).
- **Output keys spelled out.** `prior_day_high`, `prior_day_low`, `prior_day_close` — not abbreviated — so the colour swatch labels in the dialog are self-explanatory.

## Invariants
1. Output arrays are always the same length as input bars.
2. First session's bars are always NaN (no prior day to reference).
3. All bars within a session carry identical PDH/PDL/PDC values.
4. Only regular-session bars contribute to the prior day's H/L/C.
5. Auto-hidden on non-intraday intervals via `is_available_for`.

## Testing
- `tests/unit/test_prior_day_hlc.py` — 12 tests: basic two-day computation, three-day rolling, single-day all-NaN, empty input, daily bars all-NaN, constant within session, extended hours excluded, candle API, availability gating, kind_id, overlay flag, output key consistency.

