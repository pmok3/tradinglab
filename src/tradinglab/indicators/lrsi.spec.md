# indicators/lrsi.py — Spec

## Purpose
John F. Ehlers' Laguerre RSI (2002). A 4-pole Laguerre filter applied
to closes, then converted to an RSI-shaped oscillator. Reaches OB/OS
faster than a classical RSI of comparable length, with less noise in
flat-market regimes. Drawn in its own pane.

## Public API
- `class LRSI(gamma=0.5, oversold=15, overbought=85,
  show_reference_lines=True)` — `kind_id="lrsi"`, `kind_version=1`,
  `overlay=False`.
- `params_schema`:
  - `gamma: float` (default 0.5, range `[0.0, 1.0)`) — damping
    factor.
  - `oversold: int` (default 15, range `[0, 100]`).
  - `overbought: int` (default 85, range `[0, 100]`); must be
    strictly greater than `oversold`.
  - `show_reference_lines: bool` (default `True`) — when False, the
    instance reports an empty `reference_levels`.
- `default_style.lrsi`: olive `#bcbd22`, width 1.4 (distinct from
  RSI's purple).
- `scannable_outputs = (("lrsi","numeric"),)` — opts the indicator into the scanner.
- Instance `reference_levels`: `(oversold, overbought)` when
  `show_reference_lines` else `()`. Class-level `reference_levels = ()`
  so static introspection without instantiation correctly reports
  "no levels".
- `compute(candles) -> {"lrsi": ndarray}` rescaled to `[0, 100]`
  (Ehlers' published `[0, 1]` form × 100).

## Dependencies
- Internal: `..models.Candle`, `.base.LineStyle`, `.base.ParamDef`.
- External: `numpy`.

## Design Decisions
- **Output rescaled to `[0, 100]`** so LRSI shares an axis with RSI.
  Defaults 15 / 85 correspond to Ehlers' 0.15 / 0.85.
- **`gamma` trades lag for smoothness** — `γ ≈ 0.2–0.4` ⇒ fast/whippy;
  `γ ≈ 0.7–0.8` ⇒ smooth/lagging. Default 0.5 is Ehlers' classic.
- **Per-instance reference levels.** Unlike SMI / ADX (class-level
  levels), LRSI's OB/OS are user-tunable.
  `render._resolve_reference_levels` reads the instance first so each
  config gets its own axhlines on its own pane; param edits tear down
  stale lines and draw new ones on the same axis.
- **All four filter stages seeded at `closes[0]`** so the recurrence
  is defined from index 0. Outputs 0..2 are masked to NaN; LRSI is
  published from index 3 onward.
- **Flat-market output is `50`** (neutral midpoint), not NaN, when
  `CU + CD == 0` — keeps the line continuous and matches Ehlers'
  reference.
- **Validation**: `gamma ∉ [0.0, 1.0)`, OB/OS out of `[0, 100]`, or
  `oversold >= overbought` → `ValueError`.

## Invariants
- Output values lie in `[0, 100]` inclusive at every defined index.
- Indices 0–2 are NaN; finite from index 3.
- `self.reference_levels` matches the constructor toggle.

## Data Flow / Algorithm
```
L0 = (1 - γ) * close  + γ * L0_prev
L1 = -γ * L0   + L0_prev + γ * L1_prev
L2 = -γ * L1   + L1_prev + γ * L2_prev
L3 = -γ * L2   + L2_prev + γ * L3_prev

CU = sum of positive (L_k - L_{k+1}) for k in 0..2
CD = sum of positive (L_{k+1} - L_k) for k in 0..2

lrsi = 100 * CU / (CU + CD)        # 50 if CU + CD == 0
```
