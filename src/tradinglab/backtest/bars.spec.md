# backtest/bars.py — Spec

## Purpose
Per-field-ndarray bar container consumed by the engine, plus a memoised `from_candles` adapter from `List[Candle]`. The columnar layout is locked in Phase 1a so the future automated batch runner (Phase 2) doesn't have to re-port the kernel: tight fill/MAE-MFE loops walk `open` / `high` / `low` / `close` arrays directly.

## Public API
- `@dataclass(frozen=True) class BarSeries` — `symbol`, `timeframe`, `ts` (int64), `open` / `high` / `low` / `close` / `volume` (all float64). `__len__`, `index_for_ts(ts) -> Optional[int]` (exact-match `searchsorted`).
- `from_candles(symbol, timeframe, candles) -> BarSeries` — build a BarSeries from a `List[Candle]`. Cached by a content-fingerprint key (`(symbol, timeframe, len, last_ts, last_close, first_close)`); identical inputs return the same `BarSeries` object.

### Test utilities
- `_clear_cache_for_tests()` — drops the memoisation map between smoke checks. Underscore-prefixed by convention; not part of the consumer-facing surface.

## Dependencies
- Internal: `..models.Candle`.
- External: `numpy`.

## Design Decisions
- **All prices float64, ts int64 epoch seconds**. Enforced in `__post_init__`. Volume is `float64` (not int) so a "no trades" bar can be encoded as `0.0` without hand-coercion. A zero-volume bar is a valid bar, NOT a sentinel for "missing data" — missing bars are absent from `ts` entirely.
- **Adapter cache keyed by content fingerprint, not `id()`**. The fingerprint is `(symbol, timeframe, len, last_ts, last_close, first_close)` — cheap to compute, collisions only cost a re-extract (never wrong data).
- **Cache cap = 64, FIFO eviction**. `from_candles` is called once per (symbol, session) under normal flow; 64 is generous.
- **Naive datetimes treated as UTC**, tz-aware values converted. Daily candles are typically tz-naive midnight; intraday from yfinance is tz-aware.

## Invariants
- All five price arrays + `volume` share `len(ts)`. Constructor raises on mismatch.
- `index_for_ts(ts)` is exact-match: returns `None` for any ts not in the array.
- Two `from_candles` calls with the same `(symbol, timeframe, candles_content)` return the **same** `BarSeries` object (`is`-equal).

## Testing
- `check_f0_backtest_kernel` §A: BarSeries construction + `index_for_ts`.
- `check_f1_session_reproducibility` relies on identical-input idempotency.

