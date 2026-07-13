# data/prefetch/planner.py — Spec

## Purpose
Per-source **band planning**: "band = one maximal API request." Maps
`(symbol, interval, band_index)` → a `FetchWindow` request descriptor,
newest-first, exhausting the provider (Decision 8).

## Public API
- `ALPACA_MAX_PAGE = 10_000`.
- `@dataclass(frozen=True) FetchWindow(interval, kind="period", period=None,
  start=None, end=None, limit=None)` — `kind` ∈ {`"period"`, `"range"`}.
- `PeriodWindowPlanner(period_table=None, default_period="max")` — yfinance-style.
- `RangeWindowPlanner(max_page=ALPACA_MAX_PAGE)` — Alpaca-style.
- `planner_for(*, supports_range: bool) -> WindowPlanner`.
- Planner method: `band(symbol, interval, band_index, *, oldest_ts=None)
  -> FetchWindow | None`.

## Contract
- **Period (yfinance):** `band(_, iv, 0)` → `FetchWindow(kind="period",
  period=maxperiod(iv))` (1m→7d; 2m/5m/15m/30m/90m→60d; 60m/1h→730d;
  1d/1wk/1mo/unknown→"max"). `band(_, _, k≠0)` → `None` (no deeper intraday).
- **Range (Alpaca):** `band(_, iv, 0)` → `FetchWindow(kind="range", end=None,
  limit=max_page)` (most recent page). `band(_, iv, k>0, oldest_ts=T)` →
  `FetchWindow(kind="range", end=T, limit=max_page)`. `band(k>0, oldest_ts=None)`
  → `None` (needs the previous band's boundary). `band(k<0)` → `None`.
- **One-HTTP-page-per-band (review):** the range fetch primitive MUST be
  "most-recent `limit` bars with ts **strictly before** `end` (`sort=desc`)" =
  **one HTTP request = one token**. `end` is exclusive so bands don't overlap;
  `end=oldest_ts` step-back is exact under that primitive. Deepening stops when
  `oldest_ts` fails to advance (scheduler-owned), not by planner index.
- Foreground (band −1) is NOT planned here — the scheduler supplies an explicit
  window for the exact user-requested slice.
- Pure: the scheduler translates a `FetchWindow` into the real fetcher /
  range-page call at dispatch; exhaustion is detected by the scheduler from
  fetch results, not signalled by the planner (for range providers).

## Design Decisions
- `period_table` / `max_page` are injectable for tests + future providers.
- Deepening is result-driven for range providers (`oldest_ts`), so no fragile
  calendar-span math per interval.

## Testing
`tests/unit/data/prefetch/test_planner.py` — period table, period no-deeper-band,
range band-0 / step-back / no-boundary, custom page size, factory, frozen window,
negative-band rejection.
