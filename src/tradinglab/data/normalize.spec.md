# data/normalize.py — Spec

## Purpose
Format-specific vectorized translators from provider native shapes to `List[Candle]`, plus a **prebuilt-arrays side channel** so `core/series.SeriesArrays` construction doesn't re-extract the same columns. Two translators today: `candles_from_dataframe` (pandas, used by yfinance) and `candles_from_json_rows` (generic vendor-JSON, used by Schwab / Alpaca / Polygon adapters via `data/__init__.py`).

## Public API
- `@dataclass(frozen=True) class CandleArrays` — column-major NumPy view: `opens`, `highs`, `lows`, `closes`, `volumes` (all `np.ndarray`, same length). `volumes` is `float64` (not int64) to match `SeriesArrays`'s `np.nanmax` requirements for gap bars.
- `stash_arrays(candles, arrays)` — register pre-extracted arrays for a candle list. Keyed by `id(candles)`; stored as `(candles_ref, arrays)` tuple so identity is verified on pop. Bounded: `_PREBUILT_ARRAYS_MAX = 32`, oldest-evicted FIFO.
- `pop_prebuilt_arrays(candles) -> Optional[CandleArrays]` — retrieve + remove; returns `None` if the stashed entry was for a different list that happens to share this id (GC-driven id reuse defense).
- `candles_from_dataframe(df, *, interval, ohlcv_cols=None) -> List[Candle]` — columnar `.to_numpy()` extraction, `df.index.to_pydatetime()` once, **drops rows whose OHLC is not all-finite** (see Design Decisions), then a tight loop building `Candle` objects; intraday session tags come from the vectorized `classify_session_arr` (one pass over the index's hour/minute arrays) rather than a per-bar `classify_session` call. Stashes the (post-filter) arrays before returning. Used by `data/yfinance_source.fetch_live_data`.
- `candles_from_json_rows(rows, *, interval, keymap, ts_unit, tz=None) -> List[Candle]` — generic vendor-JSON → `List[Candle]` mapper. `keymap` maps logical OHLCV/ts names to vendor JSON keys; `ts_unit ∈ {"s", "ms", "ns", "iso"}`. `tz` (an exchange `tzinfo` such as `America/New_York`) converts every parsed timestamp into that zone **before** session tagging / Candle construction — REQUIRED for vendors that return UTC (Alpaca, Schwab, Polygon) so `classify_session` and the chart read US-Eastern wall-clock, matching yfinance's exchange-localized index. `tz=None` keeps the tz-aware UTC value (back-compat). Validates keymap, materializes rows once, single pass building `Candle` objects (with `classify_session` for intraday), **skipping rows whose OHLC is not all-finite** (write-cursor `j` trails the loop; arrays + list truncated to `j`). Stashes arrays. Used by Schwab / Alpaca / Polygon. Raises `ValueError` if any logical field missing from keymap.
- `_PREBUILT_ARRAYS`, `_PREBUILT_ARRAYS_MAX` (private).

## Dependencies
Internal: `..constants.classify_session`, `..constants.classify_session_arr`, `..constants.is_intraday`, `..models.Candle`. External: `numpy`, `pandas` (via duck-typed `df`).

## Design Decisions
- **`is_gap` / `gap_mask` is a data-alignment placeholder, NOT a price gap.** Gap candles are inserted by compare-mode pair alignment when timestamps don't match across tickers. ALL OHLC NaN, volume 0; indicators skip them. To detect *price* gaps, compare `open[t]` to `close[t-1]` directly.
- **Format-specific helpers**: a single generic transformer loses pandas' C-level columnar access and is measurably slower than per-row loops.
- **Columnar `.to_numpy()`** per OHLCV column is ~10× cheaper than `df.iterrows()` (each iterrows iteration constructs a fresh `Series`). On a ~5k-bar intraday fetch, vectorized extraction is 5–20× faster.
- **`df.index.to_pydatetime()` once, not per row** (per-row `.to_pydatetime()` is quadratic).
- **Candle volume as int with NaN→0 coercion** (`np.nan_to_num(volumes, nan=0.0).astype(np.int64, copy=False)` in the DataFrame path): Yahoo emits NaN/0 for extended-hours bars (volume aggregation excludes TRF tape). Raw `int()` on NaN raises `ValueError` on modern numpy. The stashed `CandleArrays.volumes` side channel remains `float64`.
- **Drop non-finite-OHLC rows.** Both translators discard any input row whose `open`/`high`/`low`/`close` is not all-finite (NaN or ±Inf). Providers (Yahoo especially) emit a placeholder row for the current/next session BEFORE any trades print — NaN OHLC, sometimes with a stray volume. Left in, it became a NON-gap candle with NaN OHLC that renders as an invisible candle (NaN body/wick verts) behind a *visible* volume bar — the "today's OHLC is missing but I can still see the volume" bug. A bar with no price is not a valid bar. NOTE: only **OHLC** gates row validity — a finite-OHLC bar with NaN/0 volume is legitimate (extended-hours) and is kept (volume → 0). This is distinct from a *gap* candle (§ `is_gap`), which is deliberately inserted with all-NaN OHLC for compare-mode alignment and never originates from a provider row. DataFrame path masks vectorized (`np.isfinite(...) & ...`); JSON path skips per-row via a write cursor. Pinned by `tests/unit/test_data_normalize_finite_ohlc.py`.
- **Prebuilt-arrays side channel**: first consumer (`build_series_safe` → `SeriesArrays.from_arrays`) pops and uses arrays directly, skipping five `np.fromiter` passes. Stash lifetime is milliseconds.
- **`(candles_ref, arrays)` tuple in the stash, not just `arrays`**: Python reuses `id()` after GC. A naive `{id → arrays}` map would hand stale arrays to a different list. `pop_prebuilt_arrays` verifies `stashed_candles is candles` and returns `None` on mismatch. **Concrete bug this fixed**: AMD's pair-aligned daily candles received SPY's arrays after compare-mode interval switches, producing SPY's y-axis range on AMD's price panel.
- **Bounded stash (32, FIFO)**: defense in depth if pop-and-consume ever breaks. Dict preserves insertion order; `next(iter(d))` gives oldest. 32 >> `_fetch_executor.max_workers=8`, so legitimate flows never evict.
- **Session classification is vectorized (DataFrame path).** `candles_from_dataframe` computes intraday session tags in one pass via `constants.classify_session_arr` over the index's `hour`/`minute` arrays instead of a per-bar `classify_session` call. Bit-for-bit identical (pinned by `tests/unit/data/test_classify_session_arr.py`). Measured on 500k 1m bars (`tools/profile_normalize_sessions.py`): the classify step alone is ~3.4× faster (82→25 ms), but the **net** session-tagging speedup is ~1.4× (59 ms) because the vectorized path must extract `df.index.hour`/`.minute` (~33 ms). **End-to-end `candles_from_dataframe` gains only ~2–3%** — the function is dominated by `to_pydatetime()` + per-bar `Candle` construction (a `slots=True` Candle is only 1.05×; a `.tolist()`-first build loop 1.13× — both potential follow-ups). `candles_from_json_rows` still classifies per-row (its cost is dominated by timestamp parsing; not yet vectorized). Intraday-only; daily+ is all `"regular"`.

## Invariants
- `pop_prebuilt_arrays(cs)` returns `None` unless `cs` is the exact list object stashed (identity check).
- `_PREBUILT_ARRAYS` size ≤ `_PREBUILT_ARRAYS_MAX`.
- **`_PREBUILT_ARRAYS` thread contract** — single-writer / single-reader-per-key: each entry built once by the producer thread, then read-only for consumers. Lifetime bounded by the candle list's GC.
- For every `i`, `candles[i].open == opens[i]` (same for H/L/C/V) — arrays and candles agree cell-by-cell.
- Non-intraday candles all have `session="regular"`; intraday `session` equals `classify_session(date.hour, date.minute)` for every bar — the DataFrame path computes it via the vectorized, bit-for-bit-identical `classify_session_arr`.
- Normalised candles preserve the provider/index timezone in the DataFrame path; JSON epoch / ISO inputs are normalised to aware UTC and then, when the adapter passes `tz`, converted into that exchange zone. Session classification uses the timestamp's own hour/minute, so UTC-returning vendor adapters (Alpaca / Schwab / Polygon) MUST pass `tz=core.timezones.ET` — omitting it leaves bars in UTC and shifts the whole intraday session +5h (the "5m data only shows 14:30–16:00" bug).
- `volumes` in `stash_arrays` is float64; `Candle.volume` is plain `int` — dual representation.
- Every returned `Candle` has all-finite OHLC (non-finite-OHLC provider rows are dropped before construction); the stashed `CandleArrays` is length-aligned with the returned list after any drop.

## Algorithm
```
candles_from_dataframe(df, interval):
    if df.empty: return []
    for col in OHLCV: arr = df[col].to_numpy(dtype=float64)
    dts = df.index.to_pydatetime()
    finite = isfinite(open)&isfinite(high)&isfinite(low)&isfinite(close)
    if not finite.all(): filter every array + dts by finite   # drop placeholder rows
    volumes_int = nan_to_num(volumes, 0.0).astype(int64)
    if is_intraday(interval):
        sessions = classify_session_arr(index.hour, index.minute)   # one vectorized pass
        for i: candles[i] = Candle(..., session=sessions[i])
    else:
        for i: candles[i] = Candle(..., session="regular")
    stash_arrays(candles, CandleArrays(...))       # side channel
    return candles
```
