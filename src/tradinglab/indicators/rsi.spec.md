# indicators/rsi.py — Spec

## Purpose
Wilder's Relative Strength Index over close prices. Values in `[0, 100]`. Non-overlay (draws in its own pane).

## Public API
- `class RSI(length=14)` — `kind_id="rsi"`, `kind_version=1`,
  `name = f"RSI({length})"`, `overlay = False`.
  `compute(candles) -> {"rsi": ndarray}` with the first `length`
  entries NaN. Raises `ValueError` on `length < 2`.
  - `params_schema = (ParamDef("length", "int", 14, min=2,
    max=2000),)` — schema enforces `length >= 2` at dialog level.
  - `default_style = {"rsi": LineStyle(color="#d62728", width=1.4)}` via `_palette.QUATERNARY`.
  - `scannable_outputs = (("rsi", "numeric"),)` — opts the indicator into the scanner / entries / exits dropdowns via the registry-driven projection in `scanner.fields`.
  - `warmup_bars` property returns `4 * length` for strategy-tester
    hydration beyond the first finite RSI point.
  - **Incremental protocol (compute #3):** `inc_init(bars)` / `inc_step(state, bars, *, prev_len)` extend RSI O(k) on a closed-bar append instead of a full O(N) recompute (~200× per 1-bar tick on an 11k-bar series). State = `{avg_gain, avg_loss, last_close, seeded}` plus the cached `output`/`len`. `inc_step` continues the Wilder recurrence `S_i = S_{i-1}·(L-1)/L + v_i/L` on the gain/loss derived from each new close. The kernel is causal so the cached prefix is bit-identical; the appended bars differ from the vectorized `wilder_smooth_avg` by float64 round-off only (~3e-14 over 300 appends). `inc_step` **raises** (→ cache full-recompute) on non-growth or when `state["seeded"]` is False (pre-warmup appends re-seed the average non-trivially). Pinned by `tests/unit/test_incremental_indicators_wilder.py`.

## Dependencies
- Internal: `..core.bars.Bars`, `.base.BaseIndicator`,
  `._palette.QUATERNARY`, `.base.LineStyle`, `.base.ParamDef`,
  `.wilder.wilder_smooth_avg`.
- External: `numpy`.

## Design Decisions
- **Wilder's smoothing** (not Cutler's): seed with a simple arithmetic mean of the first `length` deltas, then recursive `avg = (avg*(n-1) + new) / n`. This is the canonical RSI definition; matches TradingView defaults.
- **First RSI point lands at index `length`** (not `length-1`): the first `length` deltas seed the averages; the resulting RS is posted at index `length`.
- **`al == 0` → RSI = 100** (not NaN): matches the standard convention for "no losses in the window".
- **Shared Wilder kernel.** RSI delegates average gain/loss smoothing to `wilder_smooth_avg`, the same vectorized RMA primitive used by ADX / ATR-family consumers.
- **`np.diff` + `np.where` to split gains/losses** vectorized up front — cheap, and keeps the hot path branch-free before smoothing.
- **Not session-aware** — RSI runs over whatever bars it is fed, including pre/post-market bars when extended-hours rendering is on. To get a regular-hours-only RSI, drive it from a regular-only candle stream.

## Invariants
- `RSI(n).compute(cs)["rsi"]`: length `len(cs)`, entries `[0..n-1]` are NaN (indices 0 through n-1 are NaN; rsi[n] is the first defined value).
- All defined entries are in `[0.0, 100.0]`.
- Short input (`len(cs) <= n`): all-NaN output.
- `n < 2` → `ValueError` at construction.

## Data Flow / Algorithm
If `avg_gain == 0`, the formula naturally yields RSI = 0; if
`avg_loss == 0`, we shortcut to RSI = 100 to avoid division by zero.
RSI ∈ [0, 100] inclusive at every defined index.

```
deltas = diff(closes)
gains = where(deltas > 0, deltas, 0)
losses = where(deltas < 0, -deltas, 0)
avg_gain = wilder_smooth_avg(gains, n)
avg_loss = wilder_smooth_avg(losses, n)
ag = avg_gain[n-1:]
al = avg_loss[n-1:]
rs = where(al > 0, ag / al, inf)
out[n:] = 100 - 100/(1 + rs)
```
