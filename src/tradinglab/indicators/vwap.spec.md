# indicators/vwap.py — Spec

## Purpose
Session-anchored intraday Volume-Weighted Average Price. Cumulative
`Σ(price·volume) / Σ(volume)` accumulates from the start of each
calendar trading day and resets at the next day boundary. Overlay on
the price axis. Returns all-NaN on daily+ intervals (each bar is its
own session), so the line auto-hides.

## Public API
- `class VWAP(price_source="typical")` — `kind_id="vwap"`,
  `kind_version=1`, `overlay=True`. Display registry key: `"VWAP"`.
- `params_schema`:
  - `price_source: choice` (default `"typical"`, choices
    `typical | close | ohlc4`) — `typical = (H+L+C)/3`,
    `ohlc4 = (O+H+L+C)/4`.
- `default_style.vwap`: purple `#9467bd`, width 1.6.
- `scannable_outputs = (("vwap","numeric"),)` and `resets_daily = True` — opts the indicator into the scanner AND declares it as session-anchored so engine/runner know to evict cached prefixes at session boundaries (see `scanner.fields.field_ref_resets_daily`).
- `compute(candles) -> {"vwap": ndarray}`. Indices where VWAP is
  undefined (warmup, pre/post bars, gap fillers, daily+ intervals)
  are NaN.

## Dependencies
- Internal: `..models.Candle`, `.base.LineStyle`, `.base.ParamDef`.
- External: `numpy`.

## Design Decisions
- **Day boundary uses the candle's own `date`**, which the data layer
  normalises to US/Eastern for US equities (see
  `data/normalize.spec.md`). For non-ET feeds VWAP resets at that
  feed's local midnight.
- **Pre/post-market bars excluded** from both cumulative sums and
  the rendered line, even when extended-hours rendering is enabled.
  Differs from TradingView's "Session" VWAP.
- **Gap fillers (`is_gap=True`) skipped entirely.**
- **Daily-or-higher detection via median bar spacing ≥ 23h** across
  the first ~30 non-gap deltas.
- **Pure compute, no anchored-session state** — sandbox replay feeds
  candles incrementally exactly the same way live data does.

## Invariants
- Output length equals input length.
- A valid intraday session's first regular bar's VWAP equals that
  bar's `price_source` value.
- All bars zero-volume → VWAP NaN throughout.
- Daily / weekly intervals → all-NaN output.

## Data Flow / Algorithm
```
cum_pv, cum_v = 0, 0
cur_day       = None
for i, c in enumerate(candles):
    if c.is_gap:        skip
    if c.session != "regular": skip   # NaN at this index
    if c.date.date() != cur_day:
        cur_day = c.date.date()
        cum_pv = cum_v = 0
    p = price_for(c, price_source)    # typical / close / ohlc4
    cum_pv += p * c.volume
    cum_v  += c.volume
    if cum_v > 0: out[i] = cum_pv / cum_v
```

## Known limitations
- **Anchored VWAP** lives in `avwap.py` — cumulative across sessions,
  meaningful on every interval.
- Day-boundary detection assumes the candle stream's `date` is in
  the desired session timezone; no rebasing here.
