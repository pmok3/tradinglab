# data/normalize.py — Spec

## Purpose
Format-specific vectorized translators from provider native shapes to `List[Candle]`, plus a **prebuilt-arrays side channel** so `core/series.SeriesArrays` construction doesn't re-extract the same columns. Two translators today: `candles_from_dataframe` (pandas, used by yfinance) and `candles_from_json_rows` (generic vendor-JSON, used by Schwab / Alpaca / Polygon adapters via `data/__init__.py`).

## Public API
- `@dataclass(frozen=True) class CandleArrays` — column-major NumPy view: `opens`, `highs`, `lows`, `closes`, `volumes` (all `np.ndarray`, same length). `volumes` is `float64` (not int64) to match `SeriesArrays`'s `np.nanmax` requirements for gap bars.
- `stash_arrays(candles, arrays)` — register pre-extracted arrays for a candle list. Keyed by `id(candles)`; stored as `(candles_ref, arrays)` tuple so identity is verified on pop. Bounded: `_PREBUILT_ARRAYS_MAX = 32`, oldest-evicted FIFO.
- `pop_prebuilt_arrays(candles) -> Optional[CandleArrays]` — retrieve + remove; returns `None` if the stashed entry was for a different list that happens to share this id (GC-driven id reuse defense).
- `candles_from_dataframe(df, *, interval, ohlcv_cols=None) -> List[Candle]` — columnar `.to_numpy()` extraction, `df.index.to_pydatetime()` once, tight loop building `Candle` objects with `classify_session` for intraday. Stashes arrays before returning. Used by `data/yfinance_source.fetch_live_data`.
- `candles_from_json_rows(rows, *, interval, keymap, ts_unit) -> List[Candle]` — generic vendor-JSON → `List[Candle]` mapper. `keymap` maps logical OHLCV/ts names to vendor JSON keys; `ts_unit ∈ {"s", "ms", "ns", "iso"}`. Validates keymap, materializes rows once, single pass building `Candle` objects (with `classify_session` for intraday). Stashes arrays. Used by Schwab / Alpaca / Polygon. Raises `ValueError` if any logical field missing from keymap.
- `_PREBUILT_ARRAYS`, `_PREBUILT_ARRAYS_MAX` (private).

## Dependencies
Internal: `..constants.classify_session`, `..constants.is_intraday`, `..models.Candle`. External: `numpy`, `pandas` (via duck-typed `df`).

## Design Decisions
- **`is_gap` / `gap_mask` is a data-alignment placeholder, NOT a price gap.** Gap candles are inserted by compare-mode pair alignment when timestamps don't match across tickers. ALL OHLC NaN, volume 0; indicators skip them. To detect *price* gaps, compare `open[t]` to `close[t-1]` directly.
- **Format-specific helpers**: a single generic transformer loses pandas' C-level columnar access and is measurably slower than per-row loops.
- **Columnar `.to_numpy()`** per OHLCV column is ~10× cheaper than `df.iterrows()` (each iterrows iteration constructs a fresh `Series`). On a ~5k-bar intraday fetch, vectorized extraction is 5–20× faster.
- **`df.index.to_pydatetime()` once, not per row** (per-row `.to_pydatetime()` is quadratic).
- **Volume as int64 with NaN→0 coercion** (`np.nan_to_num(volumes, nan=0.0).astype(np.int64, copy=False)`): Yahoo emits NaN/0 for extended-hours bars (volume aggregation excludes TRF tape). Raw `int()` on NaN raises `ValueError` on modern numpy.
- **Prebuilt-arrays side channel**: first consumer (`build_series_safe` → `SeriesArrays.from_arrays`) pops and uses arrays directly, skipping five `np.fromiter` passes. Stash lifetime is milliseconds.
- **`(candles_ref, arrays)` tuple in the stash, not just `arrays`**: Python reuses `id()` after GC. A naive `{id → arrays}` map would hand stale arrays to a different list. `pop_prebuilt_arrays` verifies `stashed_candles is candles` and returns `None` on mismatch. **Concrete bug this fixed**: AMD's pair-aligned daily candles received SPY's arrays after compare-mode interval switches, producing SPY's y-axis range on AMD's price panel.
- **Bounded stash (32, FIFO)**: defense in depth if pop-and-consume ever breaks. Dict preserves insertion order; `next(iter(d))` gives oldest. 32 >> `_fetch_executor.max_workers=8`, so legitimate flows never evict.
- **Session classification uses a Python loop**: function is ~3 comparisons; vectorizing adds complexity without measured win. Intraday-only.

## Invariants
- `pop_prebuilt_arrays(cs)` returns `None` unless `cs` is the exact list object stashed (identity check).
- `_PREBUILT_ARRAYS` size ≤ `_PREBUILT_ARRAYS_MAX`.
- **`_PREBUILT_ARRAYS` thread contract** — single-writer / single-reader-per-key: each entry built once by the producer thread, then read-only for consumers. Lifetime bounded by the candle list's GC.
- For every `i`, `candles[i].open == opens[i]` (same for H/L/C/V) — arrays and candles agree cell-by-cell.
- Non-intraday candles all have `session="regular"`; intraday have `session = classify_session(date.hour, date.minute)`.
- Normalised candles carry US/Eastern timestamps for US equities. Session classification against ET market hours.
- `volumes` in `stash_arrays` is float64; `Candle.volume` is plain `int` — dual representation.

## Algorithm
```
candles_from_dataframe(df, interval):
    if df.empty: return []
    for col in OHLCV: arr = df[col].to_numpy(dtype=float64)
    volumes_int = nan_to_num(volumes, 0.0).astype(int64)
    dts = df.index.to_pydatetime()
    if is_intraday(interval):
        for i: candles[i] = Candle(..., session=classify_session(dts[i].hour, dts[i].minute))
    else:
        for i: candles[i] = Candle(..., session="regular")
    stash_arrays(candles, CandleArrays(...))       # side channel
    return candles
```
