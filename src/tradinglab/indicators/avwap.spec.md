# indicators/avwap.py — Spec

## Purpose
Anchored Volume-Weighted Average Price. Cumulates
`Σ(price·volume) / Σ(volume)` from a user-chosen anchor bar instead of
resetting at each session like `vwap.py`. Works on every interval
(1m → 1mo); supports optional ±1σ / ±2σ bands via Welford weighted
variance. Overlay on price axis. Anchor is picked via the dialog's
"Pick Anchor…" button (see `gui/indicator_dialog.py`,
`gui/interaction.py`).

## Pick Anchor UX

When the user clicks the "Pick Anchor…" button (either in the
Manage Indicators dialog or in a per-indicator popup):

1. `IndicatorDialog._on_pick_anchor(row)` calls
   `ChartApp._begin_anchor_pick(config_id)`.
2. `_begin_anchor_pick` iconifies **every visible indicator dialog**
   so the chart underneath is unobstructed and the user can click
   any candle without first moving the popup. This covers BOTH:
   - the Manage Indicators dialog (`self._indicator_dialog`), AND
   - every per-indicator dialog in `self._per_indicator_dialogs`
     (any one may overlap the chart; the user typically clicks Pick
     Anchor from one of these).
   Each dialog's prior state (`"normal"` / `"zoomed"` / `"iconic"` /
   `"withdrawn"`) is captured before iconifying — already-iconic
   dialogs are left alone. Audit
   `avwap-anchor-pick-iconifies-per-indicator-dialog`.
3. Cursor flips to `crosshair`; status hint reads
   "Click a bar to anchor VWAP — Esc to cancel".
4. `InteractionMixin._on_button_press` short-circuits the next
   left-click to `_handle_anchor_pick_click`, which snaps to the
   nearest non-gap regular-session bar at or after the click and
   updates `cfg.params["anchor_ts"]` via
   `IndicatorManager.update(config_id, params=...)`. `price_source`
   and `bands` are preserved (merge).
5. On success / Esc / cancel, `_cancel_anchor_pick` restores every
   previously-iconified dialog to its captured prior state and lifts
   it back over the chart so the user can keep editing.

Pinned by `tests/unit/gui/test_avwap_anchor_pick_iconify.py`
(per-indicator + Manage Indicators + multiple per-indicator + dead
dialog + no-dialog edge cases) and the `check_d42_avwap_*` sub-test
in `tests/smoke/test_smoke_full.py`.

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
- `effective_output_keys(cls, params) -> tuple[str, ...]` — classmethod overriding `BaseIndicator.effective_output_keys` for the in-readout overlay legend (`gui/readout_legend.py`). Returns ONLY the bands that the current `bands` param actually renders, in canonical top-down visual order on the chart:
  - `bands="off"` → `("avwap",)`
  - `bands="1σ"` → `("upper1", "avwap", "lower1")`
  - `bands="2σ"` → `("upper2", "avwap", "lower2")`
  - `bands="both"` → `("upper2", "upper1", "avwap", "lower1", "lower2")`
  This is what fixes the "AVWAP shows 5 legend rows when bands are disabled" bug — the `compute(...)` output schema still always returns all 5 keys (invariant 2 below), but the legend only renders the visible subset.
- `legend_label(cls, display_name, params) -> str | None` — classmethod overriding `BaseIndicator.legend_label` so the consolidated readout-legend row shows only the **anchor point** (the only "important detail" for an anchored indicator) instead of rendering every schema param. Returns:
  - bare ``Anchored VWAP`` (or whatever `display_name` is set to) when `anchor_ts` is blank — i.e. the user hasn't picked an anchor yet and the compute layer is falling back to the first eligible bar;
  - ``Anchored VWAP(2025-09-15)`` for date-only anchors (daily / weekly / monthly intervals);
  - ``Anchored VWAP(2025-09-15 09:30)`` for intraday anchors — the ISO ``T`` separator becomes a space and a trailing zero-seconds suffix is dropped for readability;
  - ``Anchored VWAP(2025-09-15 09:31:45)`` when the anchor's seconds are non-zero (precise anchor — preserved verbatim).
  ``price_source`` and ``bands`` never appear in the label (rendering knobs, not "important details"). Audit ``avwap-anchor-only-label``.
- `_format_anchor_for_label(anchor_ts: str) -> str` — module-level helper backing the `legend_label` override. Pure: date-only strings pass through; datetime strings parsed via `datetime.fromisoformat` then formatted with the rules above; unparseable strings fall back to a `T → space` substitution so the legend never raises.
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
