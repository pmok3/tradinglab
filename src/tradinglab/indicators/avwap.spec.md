# indicators/avwap.py — Spec

## Purpose
Anchored Volume-Weighted Average Price. Cumulates
`Σ(price·volume) / Σ(volume)` from a user-chosen anchor bar instead of
resetting at each session like `vwap.py`. Works on every interval
(1m → 1mo); supports optional ±1σ / ±2σ bands via Welford weighted
variance. Overlay on price axis. Anchor is picked via the dialog's
"Pick Anchor…" button (see `gui/indicator_dialog.py`,
`gui/interaction.py`).

**Anchors are symbol-keyed.** A single AVWAP config can render on the
primary AND compare panes (different tickers); each pane resolves its
OWN anchor from `params["anchors"][SYMBOL]`. An optional shared mode
(`params["anchor_shared"]`) pins ONE anchor (`shared_anchor_ts`) that
applies to every symbol. A symbol with no anchor draws nothing and the
readout reads "Not set" — there is NO auto-anchor default.

## Pick Anchor UX

When the user clicks the "Pick Anchor…" button (either in the
Manage Indicators dialog or in a per-indicator popup):

1. `IndicatorDialog._on_pick_anchor(row)` calls
   `ChartApp._begin_anchor_pick(config_id)`.
2. `_begin_anchor_pick` **withdraws** (fully hides) **every visible
   indicator dialog** so the chart underneath is unobstructed and the
   user can click any candle without first moving the popup. This
   covers BOTH:
   - the Manage Indicators dialog (`self._indicator_dialog`), AND
   - every per-indicator dialog in `self._per_indicator_dialogs`
     (any one may overlap the chart; the user typically clicks Pick
     Anchor from one of these).
   `withdraw` is used rather than `iconify`: on Windows `iconify`
   only minimises the dialog to the taskbar (it stays listed there
   and grabs focus for a beat), whereas `withdraw` removes the window
   entirely. Each dialog's prior state (`"normal"` / `"zoomed"` /
   `"iconic"` / `"withdrawn"`) is captured before hiding — dialogs
   that are already hidden (`"iconic"` / `"withdrawn"`) are left
   untouched so they aren't force-shown on restore. Audit
   `avwap-anchor-pick-iconifies-per-indicator-dialog`.
3. Cursor flips to `crosshair`; status hint reads
   "Click a bar to anchor VWAP — Esc to cancel".
4. `InteractionMixin._on_button_press` short-circuits the next
   left-click to `_handle_anchor_pick_click`, which snaps to the
   nearest non-gap regular-session bar at or after the click and
   writes the anchor into the config via
   `IndicatorManager.update(config_id, params=...)`. WHERE it writes
   depends on the config's mode and the SLOT the click landed in:
   - **Per-symbol mode** (default): `params["anchors"][SYMBOL]` where
     `SYMBOL` is the ticker shown in the clicked slot
     (`ChartApp._slot_symbol`). Picking on the compare pane anchors the
     compare ticker; the primary ticker's anchor is untouched.
   - **Shared mode** (`anchor_shared` checked):
     `params["shared_anchor_ts"]` — the one anchor for all symbols.
   `price_source` / `bands` / the other-mode slot are preserved (merge).
5. On success / Esc / cancel, `_cancel_anchor_pick` `deiconify`s every
   previously-hidden dialog back to its captured prior state and lifts
   it back over the chart so the user can keep editing.

Pinned by `tests/unit/gui/test_avwap_anchor_pick_iconify.py`
(per-indicator + Manage Indicators + multiple per-indicator + dead
dialog + no-dialog edge cases) and the `check_d42_avwap_*` sub-test
in `tests/smoke/test_smoke_full.py`.

## Public API
- `class AnchoredVWAP(anchor_ts="", anchor_shared=False,
  price_source="typical", bands="off", anchors=None,
  shared_anchor_ts="")` — `kind_id="avwap"`, `kind_version=1`,
  `overlay=True`. Display registry key: `"Anchored VWAP"`. `anchor_ts`
  is the EFFECTIVE scalar anchor the compute uses; the render layer
  injects it per slot via `resolve_anchor_ts`. The `anchors` map /
  `shared_anchor_ts` / `anchor_shared` fields are stored so the config
  round-trips and so a direct (non-render) build self-resolves the
  shared anchor (`anchor_ts` defaults to `shared_anchor_ts` when shared
  mode is on and no explicit `anchor_ts` was passed).
- `params_schema`:
  - `anchor_ts: str` (default `""`) — the effective/legacy anchor.
    Dialog renders this with a label + "Pick Anchor…" button (no
    free-text Entry); the label shows the active symbol's resolved
    anchor or "Not set". Blank effective anchor ⇒ draws nothing
    ("Not set") — there is no auto-first-eligible fallback.
  - `anchor_shared: bool` (default `False`) — the "Apply anchor to all
    symbols" checkbox. When checked, one `shared_anchor_ts` applies to
    every symbol; when unchecked, anchors are per-symbol.
  - `price_source: choice` (default `"typical"`, choices
    `typical | close | ohlc4`). **Case-insensitive on input** (`__init__`
    lowercases; audit `indicator-source-case-insensitive`).
  - `bands: choice` (default `"off"`, choices `off | 1σ | 2σ | both`).
- Non-schema params (set by the pick flow, not typed in the dialog):
  - `anchors: dict[str, str]` — `{SYMBOL_UPPER: ISO ts}` per-symbol
    anchors (the source of truth in per-symbol mode).
  - `shared_anchor_ts: str` — the anchor used in shared mode.
- `default_style`: `avwap` brown `#8c564b` via `_palette.TAB10_BROWN`
  width 1.6; band keys
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
- `legend_label(cls, display_name, params) -> str | None` — classmethod overriding `BaseIndicator.legend_label`. The legend prefix is symbol-agnostic (shared across the primary / compare panes), so it can only safely show a per-config anchor in **shared mode**:
  - bare ``Anchored VWAP`` in per-symbol mode — the anchor differs per symbol, so it surfaces as the readout value (or "Not set") rather than the prefix;
  - ``Anchored VWAP(2025-09-15)`` in shared mode for date-only anchors (daily / weekly / monthly intervals);
  - ``Anchored VWAP(2025-09-15 09:30)`` for intraday shared anchors — the ISO ``T`` separator becomes a space and a trailing zero-seconds suffix is dropped for readability;
  - ``Anchored VWAP(2025-09-15 09:31:45)`` when the shared anchor's seconds are non-zero (precise anchor — preserved verbatim).
  ``price_source`` and ``bands`` never appear in the label (rendering knobs, not "important details"). Audit ``avwap-anchor-only-label``.
- `_format_anchor_for_label(anchor_ts: str) -> str` — module-level helper backing the `legend_label` override. Pure: date-only strings pass through; datetime strings parsed via `datetime.fromisoformat` then formatted with the rules above; unparseable strings fall back to a `T → space` substitution so the legend never raises.
- `resolve_anchor_ts(params, symbol) -> str` — module-level helper returning the EFFECTIVE ISO anchor for `symbol`. Shared mode → `shared_anchor_ts` (falling back to legacy `anchor_ts`); per-symbol mode → `anchors[symbol.upper()]`; `""` when unset. Pure; called by the render layer per slot and by the readout to decide "Not set".
- `compute(candles) -> {"avwap", "upper1", "lower1", "upper2", "lower2"}`.
  Always returns all five keys; unrequested band keys are NaN-filled.
- `compute_arr(bars) -> {...}` — thin wrapper returning
  `self._compute_with_state(bars)[0]`.
- `_compute_with_state(bars) -> (out, state)` — the `compute_arr` core
  that ALSO returns the final Welford state
  `{start_idx, cum_w, mean, m2}` so `inc_init` can seed an incremental
  continuation without a second pass.
- `_avwap_emit(p, v, cum_w, mean, m2, want_1, want_2, track_var)` —
  module-level helper that processes ONE regular bar of the Welford
  recurrence and returns `(avwap, upper1, lower1, upper2, lower2,
  cum_w, mean, m2)`. Shared by `_compute_with_state` and `inc_step`
  so the batch and incremental paths are byte-identical.
- `inc_init(bars)` / `inc_step(state, bars, *, prev_len)` — incremental
  protocol (see "Incremental protocol" below).
- `first_eligible_anchor_ts(candles) -> str` — ISO timestamp of the first
  non-gap regular-session bar, or `""`. Retained helper (the
  auto-materialize-on-add caller was removed when the first-eligible
  default was dropped); still usable by callers that want a sensible
  default anchor to seed a pick.

## Dependencies
- Internal: `..core.bars.Bars`, `..models.Candle`, `._palette.TAB10_BROWN`,
  `.base.BaseIndicator`, `.base.LineStyle`, `.base.ParamDef`.
- External: `numpy`, `math`, `datetime`.

## Design Decisions
- **Symbol-keyed anchors.** Anchors live in `params["anchors"]`
  (`{SYMBOL: ISO ts}`) so one config renders the right anchor on each
  pane (primary vs compare are different tickers). The render layer
  resolves the slot's symbol → effective `anchor_ts` via
  `resolve_anchor_ts` and injects it before compute. An optional
  `anchor_shared` mode pins one `shared_anchor_ts` for every symbol
  (e.g. a macro/Fed event). Migrated legacy configs (single
  symbol-blind `anchor_ts`) land in shared mode so behaviour is
  preserved (see `config.spec.md`).
- **Unset anchor ⇒ "Not set", no line.** A symbol with no resolved
  anchor produces all-NaN output (no line) and the readout legend shows
  "Not set". The previous auto-first-eligible default was deliberately
  removed so an unanchored symbol is explicit, not silently anchored to
  bar 0.
- **Anchor stored as ISO string in `params`.** Persists across
  save/load and timeframe changes. Explicit anchors snap to the first
  non-gap `Bars` timestamp at or after the anchor and emit only on
  regular bars.
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
8. A blank/unset effective `anchor_ts` ⇒ every output key is all-NaN
   (no auto-anchor). `resolve_anchor_ts` returns `""` for a symbol with
   no anchor in per-symbol mode.

## Data Flow / Algorithm
```
anchor_dt = parse(effective anchor_ts) or None  (None ⇒ all-NaN, "Not set")
start_idx = first non-gap regular bar at/after anchor_dt  (None ⇒ all-NaN)
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

## Incremental protocol (closed-bar appends)
Anchored VWAP is a running Welford recurrence from a **fixed** anchor:
appending closed bars at the end never moves the anchor, so the
accumulation simply extends `O(k)` from the committed
`(cum_w, mean, m2)`. Both the batch (`_compute_with_state`) and
incremental (`inc_step`) paths funnel each bar through the same
`_avwap_emit` helper, so an incremental continuation is **byte-identical**
to a full recompute (not merely round-off-close).

- `inc_init(bars) -> {"output", "len", seeded, [cum_w, mean, m2]}` —
  runs `_compute_with_state` once and captures the final Welford state.
  `seeded=True` only once the anchor has actually been reached
  (`start_idx is not None`); otherwise `seeded=False` and no state is
  carried.
- `inc_step(state, bars, *, prev_len)` — copies the cached `output`
  prefix, then walks `[prev_len, n)` through `_avwap_emit` from the
  carried `(cum_w, mean, m2)`. Returns the extended output plus the new
  state.
- **Fallback (full recompute) is triggered by raising:**
  `inc_step` raises `ValueError` when `n <= prev_len` (non-growth) or
  when `state["seeded"]` is false (anchor not yet reached). Per the
  cache contract (`indicators/cache.py:get_or_compute_incremental`), any
  raise falls back to a clean full recompute — so the unseeded /
  anchor-still-ahead case is always correct, just not fast.

Pinned by the registry-driven parity meta-test in
`tests/unit/indicators/test_indicator_meta.py`
(`test_incremental_parity_matches_full_recompute`), which asserts the
running incremental output equals a from-scratch recompute at every
length over a multi-day intraday RTH fixture.
