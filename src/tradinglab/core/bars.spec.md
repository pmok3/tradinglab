# core/bars.py — Spec

## Purpose
Canonical OHLCV columnar view. The single source of truth for "candle list as NumPy columns", replacing parallel evolutions in scanner / fetch / chart-render code that each used to have their own `np.fromiter` paths.

## Public API
- `@dataclass(frozen=True) Bars` — frozen columnar view of a time series.
  - Fields: `open / high / low / close` (`float64`, shape `(n,)`), `volume` (`float64`), `timestamps` (`datetime64[ns]`, naive UTC), `session` (`object`, tags `"regular" | "pre" | "post" | "gap"`), `candles: Optional[List[Candle]]` (optional back-reference; not in `repr` or equality).
  - `__len__()` returns the number of bars.
  - `from_candles(candles: Sequence[Candle]) -> Bars` — **single-pass** OHLCV + timestamp + session extraction (one Python loop fills six pre-allocated numpy arrays + the object session array). The canonical OHLCV builder. Empty input is supported.
  - `from_arrays(*, open, high, low, close, volume, timestamps=None, session=None, candles=None) -> Bars` — construct from pre-extracted arrays. Missing `timestamps`/`session` are derived from `candles` if provided (timestamps via the same fast `_epoch_ns` path), else filled with dtype-correct sentinels (`""` for session is *not* used — defaults to `"regular"`).
  - `typical_price() -> np.ndarray` — `(high + low + close) / 3`. VWAP / classic-pivot input.

## Module helpers
- `_to_naive_utc(dt) -> datetime` — strip tzinfo after converting to UTC. Retained as the documented single conversion point for callers that need a Python datetime (e.g. `bars_buffer` single-bar streaming appends).
- `_epoch_ns(dt) -> int` — naive-UTC epoch **nanoseconds**, bit-identical to `np.datetime64(_to_naive_utc(dt), "ns")` but without a per-bar `astimezone` object allocation. tz-aware path uses the fast C `datetime.timestamp()` (`round(ts*1e6)*1000`); naive path uses integer `calendar.timegm(dt.timetuple())*1e6 + microsecond` (so a naive wall clock is taken verbatim as UTC, matching `_to_naive_utc`). `timestamps` arrays are accumulated as `int64` then reinterpreted with a zero-copy `.view("datetime64[ns]")`.

## Dependencies
- Internal: `..models.Candle`.
- External: `numpy`.

## Design Decisions
- **Frozen dataclass**: a `Bars` value is safe to share across threads without locks. Arrays are not copy-on-write — callers must treat them as read-only.
- **`volume` is `float64`, not `int64`**: the chart's `SeriesArrays` uses `np.nanmax` for gap-tolerant axis scaling. Float-from-the-start avoids a per-render `astype`.
- **`timestamps` is `datetime64[ns]`, naive UTC**: matches scanner's prior `BarsNp` convention. `_epoch_ns()` (fast, no per-bar `astimezone`) is the conversion point used by both constructors; `_to_naive_utc()` is retained for the single-bar streaming append in `bars_buffer`. Both are bit-identical for the same input; never bypass them.
- **Single-pass `from_candles`**: the former seven walks of the candle list (5× `np.fromiter` for OHLCV + 2× `np.array` list-comprehension for timestamps/session) collapsed into one `for i, c in enumerate(candles)` loop over six pre-allocated arrays. Measured ~2.3× faster on an 11k-bar tz-aware ET series (~23 ms → ~10 ms), the dominant cost being the eliminated per-bar `astimezone` in the old timestamp path. Bit-for-bit identical output (pinned by the indicator/scanner equivalence suites that consume `Bars`).
- **`candles` back-reference is optional**: callers that build a `Bars` from raw arrays (e.g. fetch-side stash) can't always produce a candle list cheaply. Indicators not yet migrated to `compute_arr` fall back through `bars.candles` and refuse to run if it's `None`.
- **Replaces three predecessors**: `scanner.fields.BarsNp` (full OHLCV + ts + session), `data.normalize.CandleArrays` (OHLCV only), `core.series.SeriesArrays` (OHLCV + tooltip cache). `Bars` lifts the fullest shape (scanner's) so all consumers can share one snapshot.

## Invariants
- `len(bars.open) == len(bars.high) == ... == len(bars.timestamps) == len(bars.session) == n`.
- `bars.timestamps` is naive UTC (no tzinfo).
- `bars.volume.dtype == float64` even if constructed from int64 input (`from_arrays` coerces).
- `bars.session[i]` is one of `{"regular", "pre", "post", "gap"}`.
- If `candles is not None`, `len(candles) == n`.

## Testing
- Covered indirectly via integration smoke tests; the scanner, chart, and indicator pipelines all consume `Bars`.

