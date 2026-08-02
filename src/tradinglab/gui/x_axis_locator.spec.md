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
- `_session_starts(cs) -> list[int]` — index of each session's first
  bar (index 0 always counts). Cached per `id(candles)`.
- `_bars_per_day(cs) -> int | None` — **modal** bar count between
  session starts; `None` when fewer than two complete sessions exist
  (a single continuous run, e.g. the smoke fixture). Mode rather than
  mean so half-days and holidays don't drag the stride off-grid.
- `_step_candidates(bpd) -> list[int]` — allowed bar strides,
  ascending: divisors of `bars_per_day` plus whole-session multiples
  when known, else a generic nice-number ladder.
- `_period_for_seconds(secs) -> tuple` — nearest `_X_PERIODS` entry;
  sets `_last_period` so the formatter still picks a sane label format.
- `_uniform_intraday_ticks(cs, lo, hi) -> list[int]` — the intraday
  tick path (see Design Decisions). `[]` when the window is too narrow,
  which makes the caller fall through to the calendar-bucket path.

## Dependencies

- Internal: `..constants.is_intraday`, `..formatting.format_dt`.
- External: `numpy` (`floor` / `ceil`), `matplotlib.ticker`
  (`FixedLocator`, `FuncFormatter`) — lazy.

## Design Decisions

- **Intraday ticks step by BARS, not by wall clock.** The x-axis is
  bar-index space, but the market is shut overnight and sessions open
  at 09:30 — not on an hour boundary. So equal wall-clock periods cover
  *unequal* bar counts, and the calendar-bucket path produced visibly
  ragged axes: on RTH 5m data an hourly tick set gives gaps of
  `{6, 12}` bars within a day (the 09:30→10:00 stub is half an hour)
  and `{30, 48}` across an overnight gap. `_uniform_intraday_ticks`
  replaces it for `is_intraday(interval)` with a fixed bar stride, so
  spacing is uniform *by construction*.
  - Stride selection honours the tick budget first — the **densest**
    candidate yielding `3 ≤ ticks ≤ _TARGET`. Choosing the stride
    merely closest to `span / _TARGET` fails when `bars_per_day` has
    few divisors (26 at 15m divides only by 1, 2, 13, 26; "closest"
    lands on 2 and paints 26 ticks).
  - Preferring divisors of `bars_per_day` and anchoring on the last
    session start at-or-before the view keeps ticks at the same times
    of day, so the formatter's day-crossing upgrade still lands on each
    session's opening bar and reads `Apr 21` there.
  - On a half-day the stride keeps its spacing and the times drift for
    that session. Uniform spacing is the property worth preserving;
    a ragged axis is the thing users see.
  - Non-intraday intervals keep the calendar-bucket path unchanged.
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
