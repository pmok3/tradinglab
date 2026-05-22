# core/bars.py — Spec

## Purpose
Canonical OHLCV columnar view. The single source of truth for "candle list as NumPy columns", replacing parallel evolutions in scanner / fetch / chart-render code that each used to have their own `np.fromiter` paths.

## Public API
- `@dataclass(frozen=True) Bars` — frozen columnar view of a time series.
  - Fields: `open / high / low / close` (`float64`, shape `(n,)`), `volume` (`float64`), `timestamps` (`datetime64[ns]`, naive UTC), `session` (`object`, tags `"regular" | "pre" | "post" | "gap"`), `candles: Optional[List[Candle]]` (optional back-reference; not in `repr` or equality).
  - `__len__()` returns the number of bars.
  - `from_candles(candles: Sequence[Candle]) -> Bars` — single-source `np.fromiter` extraction; the only place that should call `np.fromiter` for OHLCV. Empty input is supported.
  - `from_arrays(*, open, high, low, close, volume, timestamps=None, session=None, candles=None) -> Bars` — construct from pre-extracted arrays. Missing `timestamps`/`session` are derived from `candles` if provided, else filled with dtype-correct sentinels (`""` for session is *not* used — defaults to `"regular"`).
  - `typical_price() -> np.ndarray` — `(high + low + close) / 3`. VWAP / classic-pivot input.

## Dependencies
- Internal: `..models.Candle`.
- External: `numpy`.

## Design Decisions
- **Frozen dataclass**: a `Bars` value is safe to share across threads without locks. Arrays are not copy-on-write — callers must treat them as read-only.
- **`volume` is `float64`, not `int64`**: the chart's `SeriesArrays` uses `np.nanmax` for gap-tolerant axis scaling. Float-from-the-start avoids a per-render `astype`.
- **`timestamps` is `datetime64[ns]`, naive UTC**: matches scanner's prior `BarsNp` convention. `_to_naive_utc()` is the single conversion point — never bypass it.
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

