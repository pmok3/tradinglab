# indicators/avwap.py — Spec

## Purpose
Anchored Volume-Weighted Average Price. Cumulates
`Σ(price·volume) / Σ(volume)` from a user-chosen anchor bar instead of
resetting at each session like `vwap.py`. Works on every interval
(1m → 1mo); supports optional ±1σ / ±2σ bands via Welford weighted
variance. Overlay on price axis. Anchor is picked via the dialog's
"Pick Anchor…" button (see `gui/indicator_dialog.py`,
`gui/interaction.py`).

## Public API
- `class AnchoredVWAP(anchor_ts="", price_source="typical", bands="off")`
  — `kind_id="avwap"`, `kind_version=1`, `overlay=True`. Display
  registry key: `"Anchored VWAP"`.
- `params_schema`:
  - `anchor_ts: str` (default `""`) — ISO-8601 anchor timestamp.
    Dialog renders this with a label + "Pick Anchor…" button (no
    free-text Entry). Blank ⇒ falls back to the first eligible bar
    until the app's `add` event hook materializes a real timestamp.
  - `price_source: choice` (default `"typical"`, choices
    `typical | close | ohlc4`).
  - `bands: choice` (default `"off"`, choices `off | 1σ | 2σ | both`).
- `default_style`: `avwap` brown `#8c564b` width 1.6; band keys
  (`upper1`, `lower1`, `upper2`, `lower2`) mid-blue `#4393c3` width
  1.0. The mid-blue clears WCAG-AA non-text contrast in both themes
  (~3.39:1 on white, ~4.92:1 on dark `#1e1e1e`).
- `scannable_outputs = (("avwap","numeric"),)` — only the AVWAP line is exposed to the scanner; bands are visual-only.
- `compute(candles) -> {"avwap", "upper1", "lower1", "upper2", "lower2"}`.
  Always returns all five keys; unrequested band keys are NaN-filled.
- `first_eligible_anchor_ts(candles) -> str` — ISO date of the first
  non-gap regular-session bar, or `""`. Used by
  `ChartApp._materialize_blank_avwap_anchors`.

## Dependencies
- Internal: `..models.Candle`, `.base.LineStyle`, `.base.ParamDef`.
- External: `numpy`, `math`, `datetime`.

## Design Decisions
- **Anchor stored as ISO string in `params`.** Persists across
  save/load and timeframe changes. Compute snaps to the first non-gap
  regular-session bar with `date >= anchor_dt`, so changing TF keeps
  the calendar instant pinned.
- **Timezone-naive comparison.** Both the parsed anchor and each
  candle's `date` have tzinfo stripped (after astimezone-to-UTC for
  aware values). Avoids `TypeError` on tz-aware feeds.
- **Skip pre/post bars.** Mirrors session VWAP's eligibility rule.
  The app's pick handler snaps pre/post clicks forward to the next
  regular bar.
- **Always emit all 5 output keys.** Bands toggle doesn't shrink the
  output schema, so the render layer's stale-output-key removal
  limitation never bites. Unrequested keys are all-NaN.
- **Welford weighted variance for bands.** Numerically stable against
  long histories and high-nominal price series (the naive
  `Σp²v / Σv − mean²` cancels catastrophically).
- **Daily/weekly/monthly are first-class.** Unlike session VWAP
  (which all-NaNs on D+ intervals), AVWAP is meaningful at any TF.

## Invariants
1. Output arrays are exactly `len(candles)` long.
2. All five output keys are always present.
3. Indices before the resolved start bar are NaN for every key.
4. Index `i` is NaN for every key when `candles[i]` is a gap or
   has `session != "regular"`.
5. When `cum_w == 0` (only zero-volume bars seen), every output is
   NaN.
6. Bands on/off does not change the `avwap` output values.
7. Compute is deterministic and pure.

## Data Flow / Algorithm
```
anchor_dt = parse(anchor_ts) or None  (None ⇒ "first eligible bar")
start_idx = first i with candles[i] non-gap, regular, _strip_tz(date) >= anchor_dt
cum_w = mean = m2 = 0.0
for i in [start_idx, n):
    c = candles[i]
    if gap or c.session != "regular": continue
    v = c.volume; if v <= 0: emit running mean / bands; continue
    p = price_for(c, price_source)
    new_w = cum_w + v
    delta = p - mean
    mean += (v / new_w) * delta
    m2   += v * delta * (p - mean)
    cum_w = new_w
    out["avwap"][i] = mean
    if bands enabled:
        std = sqrt(max(0, m2 / cum_w))
        emit ±1σ and/or ±2σ
```
