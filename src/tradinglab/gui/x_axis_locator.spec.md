# gui/x_axis_locator.py — Spec

## Purpose

TradingView-style adaptive x-axis locator + formatter for the chart's
price axes. Picks tick positions and labels that scale from 1-minute
intraday to multi-year daily: choose finest "nice" period (minute /
hour / day / week / month / year) whose `visible_span / period ≤
target ticks (~12)`; formatter upgrades labels on calendar-unit
crossings (day → month → year).

## Public API

- `_adaptive_x_locator_class() -> type` — returns the cached
  `_AdaptiveXLocator` class (`matplotlib.ticker.FixedLocator`
  subclass). Lazy on first call; matplotlib import deferred so
  non-GUI consumers don't pay the cost.
- `_make_x_formatter(app, slot_key: str) -> FuncFormatter` —
  bound to one slot of `app._panel_state`. Reads the slot's
  `price_ax` locator's `_last_period` back-ref to pick fine-label
  style (`HH:MM` / `%d` / `%b` / `%Y`); upgrades to context label
  (`%b %d` / `%b` / `%Y`) on calendar-unit crossings.

`__all__`: `_make_x_formatter`, `_adaptive_x_locator_class`.

## Module-private helpers

- `_X_PERIODS: tuple[(unit, count, seconds), …]` — 21-entry "nice
  intervals" ladder, 1 minute → 5 years.
- `_x_bucket(ts, unit, count)` — bucket key for the calendar
  step; consecutive candles in different buckets are tick
  boundaries.
- `_x_pick_period(span_seconds, target)` — smallest period whose
  `span / period ≤ target`.
- `_x_finer_period` / `_x_coarser_period` — neighbours in ladder.
- `_x_context_unit(period_seconds)` — larger unit that triggers a
  label upgrade (`day` / `month` / `year`).
- `_x_context_crosses(prev_ts, cur_ts, ctx)` — did the two
  timestamps cross the context unit?
- `_make_adaptive_x_locator_class()` — factory invoked once.

### `_AdaptiveXLocator`

- `__init__(slot_key, app, interval_name)` — holds back-ref to
  `ChartApp` for live access to `_panel_state` / `_display_tz`.
  Caches per-(`id(candles)`, period) boundary lists + per-
  `id(candles)` median bar-second.
- `_last_period: tuple` — read by formatter.
- `_TARGET: int = 12`.

## Dependencies

- Internal: `..constants.is_intraday`, `..formatting.format_dt`.
- External: `numpy` (`floor` / `ceil`), `matplotlib.ticker`
  (`FixedLocator`, `FuncFormatter`) — lazy.

## Design Decisions

- **Single cached locator class** vs redefined-per-render: class
  body doesn't depend on per-render state.
- **Back-ref to `ChartApp`, not snapshot inputs**: locator reads
  live candle list every tick (list shifts under streaming +
  rollovers).
- **`id(candles)`-keyed cache**: streaming-append preserves list
  id (in-place); rollover that builds a new list flushes the
  cache naturally via id change.
- **Intraday vs non-intraday span**: intraday uses `(hi - lo) *
  median_bar_seconds` so overnight / weekend gaps don't inflate
  period pick; non-intraday uses wall-clock delta directly.
- **`_safe_delta_seconds`**: tolerates mixed tz-aware/naive pairs
  (yfinance pickles carry tzinfo; in-memory fakes / streaming
  don't). Strips tzinfo from whichever has it (both represent
  exchange wall clock). Mirrors `core.pairing._normalize_pairing_key`.
- **Two-pass widen / four-pass tighten**: after initial pick,
  walk ladder finer up to 2 steps if `len(vis) < max(4,
  target//2)`, then coarser up to 4 steps if `len(vis) > target`.
  Lands visible count in 6–12 range without oscillating.
- **Fallback to every-Nth-bar** when no calendar boundary lies
  inside the visible window.
- **Formatter reads locator's `_last_period`**: same period pick
  that produced the ticks drives the label style.
- **`format_dt` for intraday minute/hour labels** routes through
  `_display_tz` so chart shows user's preferred timezone.

## Invariants

- `_adaptive_x_locator_class()` returns the same class object
  across calls in a process lifetime.
- `_AdaptiveXLocator._last_period` always one of the 21 tuples
  in `_X_PERIODS`.
- Formatter never raises: out-of-range `v` or missing axes /
  candles returns `""`.
- Tick positions always integer bar indices (never fractional).
