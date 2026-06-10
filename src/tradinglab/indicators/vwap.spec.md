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
- `is_available_for(interval) -> Availability` (static) — returns `intraday_only(interval)`: `ok=False` on daily / weekly / monthly, `ok=True` on intraday. Lets the chart Add-Indicator menu grey VWAP out on a daily chart AND lets the Strategy Tester's `interval_compat` guard block a Run that references VWAP on a non-intraday interval (it would resolve to NaN every bar → zero trades). Audit `intraday-interval-guard`.
- `compute_arr(bars) -> {"vwap": ndarray}`. Indices where VWAP is
  undefined (warmup, pre/post bars, gap fillers, daily+ intervals)
  are NaN; inherited `compute(candles)` forwards through `BaseIndicator`.

## Dependencies
- Internal: `..core.bars.Bars`, `._palette`, `.base.Availability`,
  `.base.BaseIndicator`, `.base.LineStyle`, `.base.ParamDef`,
  `.base.intraday_only`, `.sessions.is_intraday_np`,
  `.sessions.session_groups_np`.
- External: `numpy`.

## Design Decisions
- **Day boundary uses `session_groups_np(bars, regular_only=True)`**,
  grouping admitted regular bars by the `Bars.timestamps` calendar day
  (the data layer normalises US equities to exchange-local timestamps).
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
price = _price_arr(bars, price_source)
for grp in session_groups_np(bars, regular_only=True):
    p = price[grp]
    v = finite_volume_or_zero(bars.volume[grp])
    cum_pv = cumsum(where(isfinite(p), p * v, 0))
    cum_v  = cumsum(where(isfinite(p), v, 0))
    out[grp] = where(cum_v > 0, cum_pv / cum_v, NaN)
```

## Known limitations
- **Anchored VWAP** lives in `avwap.py` — cumulative across sessions,
  meaningful on every interval.
- Day-boundary detection assumes `Bars.timestamps` are already in the
  desired session timezone; no rebasing here.

## Incremental protocol (compute #3)
- `inc_init(bars)` / `inc_step(state, bars, *, prev_len)` extend the session VWAP O(k). State = `{cum_pv, cum_v, cur_day, seeded}` + cached `output`/`len`: accumulate `price*vol` / `vol` within the current timestamp day, RESET at each new day (`cur_day` change), and skip non-regular bars (NaN, no contribution) — mirroring compute_arr's per-group cumsum. Non-intraday inputs leave `seeded=False` (compute_arr is all-NaN there) → full recompute. Pinned by the generic parity meta-test (multi-day intraday fixture exercises the reset).
