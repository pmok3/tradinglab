# core/series.py — Spec

## Purpose
Vectorized numpy view of a `List[Candle]` plus a lazy per-candle tooltip-text cache. The OHLCV arrays are built up-front because autoscale slices them on every pan; tooltip strings are built **on demand** because most candles are never hovered. Shared by `core/viewport.y_limits_for_slice`, `ChartApp._series`, `gui/interaction._show_hover`.

## Public API
- `class SeriesArrays(__slots__=...)` — holds `opens`, `highs`, `lows`, `closes`, `volumes` (all `np.ndarray`, same length), plus `_candles` (the list it was built from), `_format_date` (callable), `_tooltip_cache: Dict[int, str]`, and `n: int`.
  - `__init__(candles, format_date)` — legacy path: five `np.fromiter` passes (one per OHLCV column).
  - `@classmethod from_arrays(candles, format_date, arrays)` — fast path when the fetcher already extracted arrays during `candles_from_dataframe`.
  - `@classmethod from_bars(bars, format_date) -> SeriesArrays` — alternate fast path for the scanner / indicator-cache layer when a NumPy-shaped `BarsNp` is already in hand. Reuses the existing OHLCV arrays in-place (no re-extraction), reconstructs a synthetic `List[Candle]` view from `bars` for the tooltip-text path, and stashes it under `_candles` so consumers that read `SeriesArrays._candles` (e.g. cache identity checks) keep working.
  - `tooltip_text(idx) -> str` — formats `"[PRE]/[POST] <date>\nO: ...\nH: ...\nL: ...\nC: ...\nVol: ..."` on first call; caches the result so repeated hovers are free.
- `build_series_safe(candles, format_date) -> Optional[SeriesArrays]` — thread-safe builder used by worker threads. Pops any prebuilt arrays (via `data.pop_prebuilt_arrays`) and takes the fast path; falls back to the legacy path; swallows exceptions.

## Dependencies
- Internal: `..data.pop_prebuilt_arrays`, `..formatting.fmt_volume`, `..models.Candle`.
- External: `numpy`.

## Design Decisions
- **`__slots__`** on `SeriesArrays`: instances are created per-candle-list (cached by `id()` in `ChartApp._series_cache`); slots cut memory ~30% vs. a `__dict__`-backed class and make attribute access a shade faster. Also documents the full attribute surface — unexpected attrs would `AttributeError`.
- **`np.fromiter` with explicit `dtype=float, count=n`** rather than `np.array([c.low for c in candles])`. The generator path avoids building an intermediate Python list (material speedup on ~5k-bar intraday series).
- **Fast-path construction via `from_arrays`**: when the fetcher (e.g. `candles_from_dataframe`) already extracted numpy arrays, it stashes them keyed by `id(candles)`. `build_series_safe` pops the stash and hands the arrays directly to `from_arrays`, skipping 5 extraction passes. See `data/normalize.py` for the side-channel.
- **Lazy tooltip cache** (dict keyed by int idx): pre-formatting every candle's tooltip at startup would burn 5–50ms on large histories. Hover latency is dominated by blit overhead anyway, so lazy-and-cache is the right tradeoff.
- **Session tag in tooltip** (`[PRE] ` / `[POST] `) so users can tell at a glance why a bar is visually dimmer.
- **`build_series_safe` is thread-safe**: `SeriesArrays.__init__` only populates numpy arrays and stashes the `format_date` callable (the callable is invoked later on the main thread from `tooltip_text`, where Tk access is safe). The builder itself reads nothing from Tk.
- **Empty-candles returns `None`**: callers (`ChartApp._series`) handle this by constructing an empty placeholder via `SeriesArrays.__new__` so the cache stays populated and the ID-identity contract holds.

## Invariants
- `SeriesArrays.lows[i] == candles[i].low` for all i (same for opens/highs/closes/volumes). `ChartApp._series` verifies identity on cache hit (`sa._candles is candles`) to guard against `id()` reuse after GC — a different list with the same len and reused id would otherwise inherit stale arrays (bug: AMD's daily candles got SPY's arrays in compare-mode interval switch).
- The fast path and the legacy path produce identical arrays (no rounding differences), because `candles_from_dataframe` already extracts as `float64` and the legacy path also uses `dtype=float`.
- `tooltip_text(idx)` is idempotent and stable — same text on every call for the same idx.

## Data Flow / Algorithm
Trivial for `SeriesArrays`. `build_series_safe`:
```
if not candles: return None
try:
    prebuilt = pop_prebuilt_arrays(candles)     # side-channel from normalizer
    if prebuilt is not None:
        return SeriesArrays.from_arrays(candles, format_date, prebuilt)
    return SeriesArrays(candles, format_date)
except Exception:
    return None                                 # main thread will rebuild lazily
```

## Testing
- Exercised transitively by `check_40_virtualized_render` (pan autoscale), `check_a0_hover_and_crosshair` (tooltip formatting), and `check_50_compare_mode` (separate SeriesArrays per slot).

## Known limitations / Future work
- `tooltip_cache` is unbounded; on a 60k-candle series if the user hovered every bar it would retain ~10MB of strings. In practice no user does this.

