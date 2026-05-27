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
  - `default_style = {"rsi": LineStyle(color="#d62728", width=1.4)}`.
  - `scannable_outputs = (("rsi", "numeric"),)` — opts the indicator into the scanner / entries / exits dropdowns via the registry-driven projection in `scanner.fields`.

## Dependencies
- Internal: `..models.Candle`.
- External: `numpy`.

## Design Decisions
- **Wilder's smoothing** (not Cutler's): seed with a simple arithmetic mean of the first `length` deltas, then recursive `avg = (avg*(n-1) + new) / n`. This is the canonical RSI definition; matches TradingView defaults.
- **First RSI point lands at index `length`** (not `length-1`): the first `length` deltas seed the averages; the resulting RS is posted at index `length`.
- **`al == 0` → RSI = 100** (not NaN): matches the standard convention for "no losses in the window".
- **Loop instead of vectorized recursion**: the recursive smoothing (`avg_gain`/`avg_loss` each depend on the previous) can't be trivially vectorized. A numpy `lfilter` would work but adds complexity for modest speedup.
- **`np.diff` + `np.where` to split gains/losses** vectorized up front — cheap, and avoids branching in the hot loop.
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
avg_gain = gains[:n].mean()
avg_loss = losses[:n].mean()
out[n] = 100 - 100/(1 + avg_gain/avg_loss)   # or 100 if avg_loss == 0
for i in n+1 .. len(closes)-1:
    g = gains[i-1]; l = losses[i-1]
    avg_gain = (avg_gain*(n-1) + g) / n
    avg_loss = (avg_loss*(n-1) + l) / n
    out[i] = 100 - 100/(1 + avg_gain/avg_loss)   # or 100 if avg_loss == 0
```

