# backtest/heatmap.py — Spec

## Purpose
Pure metric + geometry layer for the sandbox heatmap. Turns candles,
per-symbol classification, and a replay-clock timestamp into a
laid-out, colored `HeatmapModel` — a Finviz-style sector → industry
treemap sized by historically-scaled market cap and colored by 1-Day
percent change. Contains no Tk and no matplotlib so every rule is
headless-testable. The window in
[`gui/sandbox_heatmap.py`](../gui/sandbox_heatmap.spec.md) renders the
model it returns. See [`docs/SANDBOX_HEATMAP.md`](../../../docs/SANDBOX_HEATMAP.md).

## Public API
- `@dataclass(frozen=True) class Classification` — `sector: str`,
  `industry: str`. Per-symbol **static** metadata, injected. Share
  counts are time-varying (buybacks / dilution) and are **not** stored
  here — they come from a historical-shares provider and reach this
  layer already snapped, via `size_by_symbol`.
- `@dataclass(frozen=True) class HeatmapTile` — `symbol`, `sector`,
  `industry`, `size: float`, `approx_size: bool` (True when `size` used
  a carried-back share count), `x/y/w/h: float` (normalized `[0,1]`
  geometry), and post-color `pct: float | None` + `fill: str` (hex).
- `@dataclass(frozen=True) class HeatmapLayout` — `tiles: tuple[HeatmapTile, ...]`
  (geometry only, no color), `sector_bounds` / `industry_bounds`
  (group rectangles for headers + borders), `size_basis: str`.
- `@dataclass(frozen=True) class HeatmapModel` — a colored layout:
  `tiles`, `as_of_ts: int`, `timeframe: str`, `clip_pct: float`,
  `universe_id: str`.
- `members_asof(date_added_by_symbol, as_of_ts) -> tuple[str, ...]` —
  point-in-time membership filter: the current members whose `Date added`
  ≤ `as_of_ts`. Removes look-ahead names; the caller feeds the result to
  `build_layout(symbols=…)`.
- `build_layout(*, symbols, size_by_symbol, classification, approx_size_symbols=frozenset()) -> HeatmapLayout`
  — group sector → industry, run `squarify`, return geometry; tiles
  whose symbol is in `approx_size_symbols` get `approx_size=True`.
  Called at session roll only.
- `apply_colors(layout, *, pct_by_symbol, as_of_ts, clip_pct=3.0, timeframe="1D", universe_id="") -> HeatmapModel`
  — attach `pct` + Finviz `fill` per tile; stamp `as_of_ts` / `timeframe`
  / `universe_id` onto the model. Called every bar.
- `compute_1d_pct(price_at_clock, prior_close) -> float | None` —
  `(price − prior_close) / prior_close × 100`; `None` on missing input.
- `scaled_cap(shares, price) -> float` — `shares × price`. Caller must
  pass both legs on the **same split basis**; since the price series is
  back-adjusted to today, that means lifting an as-reported share count
  with `split_factor_after` (see Invariant 7).
- `split_factor_after(splits, as_of_ts) -> float` — cumulative product
  of split ratios dated **strictly after** `as_of_ts`; `1.0` for an
  empty history. Multiply an as-reported count observed at `as_of_ts`
  by this to express it on today's basis. Handles fractional ratios
  (reverse splits, vendor-recorded spin-offs); skips NaN / non-positive
  entries; ms→s normalized; input need not be sorted.
- `price_at_or_before(candles, as_of_ts) -> float | None` — close of the
  last candle at/before `as_of_ts` (no-future-leakage lookup; ms→s
  normalized; ascending candles; NaN-close bars skipped). **Intraday
  series only** — see Invariant 6.
- `session_date_of(as_of_ts) -> date` — UTC session date of an epoch
  timestamp (ms or s), identical to
  `SandboxController.current_session_date()`.
- `completed_session_closes(daily_candles, as_of_ts, *, count=1) -> tuple[float, ...]`
  — closes of the last `count` **completed** daily sessions, oldest-first.
  A daily bar counts only when its session date is *strictly before* the
  clock's session date — the same rule
  `SandboxController.daily_visible_for` applies. Short/empty tuple when
  history doesn't reach; the caller degrades to `None`, never a partial
  guess. The building block for a leak-free 1-Day % base leg.
- `squarify(values, x, y, w, h) -> list[tuple[float, float, float, float]]`
  — vendored squarified-treemap rectangle packer.
- `finviz_hex(pct, clip_pct=3.0) -> str` — % → bucketed red/green hex.
- `relative_luminance(hex) -> float` / `text_color_for(fill_hex) -> str`
  — luminance-based label-color chooser.

## Dependencies
- Internal: [`models`](../models.spec.md) (`Candle`), [`core/timezones`](../core/timezones.spec.md) (`normalize_epoch_to_seconds`).
- External: `dataclasses`, `math`, `collections` (stdlib only). No numpy, Tk, or matplotlib — the layout math is pure Python.

## Design Decisions
- **Two-phase build mirrors the update cadence.** `build_layout`
  (geometry, expensive squarify) is separated from `apply_colors`
  (per-tile fill, cheap) so the window can relayout per session and
  recolor per bar (decision 8) without re-running squarify each tick.
- **Geometry/color core never reads the clock; two clock-aware helpers.**
  `build_layout` / `apply_colors` consume caller-supplied
  `size_by_symbol` / `pct_by_symbol`, so the geometry is a pure function
  of its inputs. The clock-aware utilities are
  `price_at_or_before(candles, as_of_ts)` (intraday) and
  `completed_session_closes(daily, as_of_ts)` (daily) — pure, testable
  lookups that enforce the no-future-leakage boundary at the price-fetch
  site. They are deliberately **two** functions because the two bar
  kinds need opposite rules: an intraday bar at/before the clock is
  information the trader had, whereas a *daily* bar is timestamped at
  its open but carries the settled close, so "at/before the clock"
  admits the in-progress session's final print. Using the intraday rule
  on daily bars is exactly the leak that made the map show the finished
  day's move from the opening bar onward (and, since both legs were then
  constant all day, never change intraday). ms→s normalization comes
  from the shared `core.timezones.normalize_epoch_to_seconds`, so the
  `1e12` ms/s threshold lives in exactly one place. The caller composes
  them into `size_by_symbol` / `pct_by_symbol`.
- **Historically-scaled cap, not current cap** (decision 3). `size` is
  `scaled_cap(shares_at_session, session_reference_price)` so tile area
  reflects the historical moment. `shares_at_session` is the caller's
  historical share count (the `shares_data_source` provider (SEC EDGAR), most-recent filed value
  ≤ the session), capturing buybacks / dilution — not a constant —
  **lifted onto the price series' basis** by `split_factor_after`
  (Invariant 7). **Before the series starts** the caller carries back
  the earliest known count (nearest-in-time, never today's) and flags
  the symbol so its tile is `approx_size`.
- **1-Day % is the only color metric in v1** (decisions 4, 5). The
  color basis is a single injected `pct_by_symbol` map; a future RS /
  vs-SPY or custom-RS basis is a drop-in different map, so no signature
  change is needed to add it.
- **Finviz-exact fixed palette** (decision 11). `finviz_hex` buckets %
  into the Finviz red ↔ neutral ↔ green steps clipped at `±clip_pct`
  (default 3.0); the scale is fixed, never auto-ranged, so bar-to-bar
  color change is meaningful.
- **Vendored squarify, no dependency** (decision 6). The ~40-line
  algorithm is deterministic and unit-testable; adding a PyPI treemap
  package for it is not worth the release-surface cost.
- **Unknown classification is grouped, not dropped.** A symbol whose
  `Classification` is missing or empty lands in an `Unclassified`
  sector so the map stays complete.
- **Point-in-time membership via `Date added`** (v1 survivorship
  stance). `members_asof` drops current members added after the replay
  clock, so look-ahead names never appear; composition changes as the
  clock crosses an add date (the caller handles it like a session roll).
  Removed / delisted names are a documented residual — recovering them
  needs a changes-log (v2). The caller resolves members by CIK / name,
  not bare ticker, to avoid recycled-ticker mismatches.

## Invariants
1. `squarify` output rectangles tile the parent exactly — Σ areas ==
   `w × h` within float epsilon — with no negative or zero dimensions
   for positive input values.
2. `squarify` is deterministic: identical input order → identical
   geometry.
3. Every input `symbol` appears in exactly one `HeatmapTile`; grouping
   is strictly sector → industry; missing metadata → `Unclassified`.
4. `apply_colors` never mutates `layout`; it returns a new
   `HeatmapModel`.
5. Color is symmetric about 0 and clipped to `[−clip_pct, +clip_pct]`;
   `pct is None` (missing data) maps to the neutral fill, never a
   red/green extreme.
6. No value is read from any candle beyond `as_of_ts`. Enforced at the
   two lookup helpers: `price_at_or_before` stops at the clock, and
   `completed_session_closes` admits a daily bar only when its session
   date is strictly before the clock's. **A daily bar's timestamp is its
   open but its close is the session's last print**, so daily bars must
   never go through the at-or-before rule. Pinned by
   `tests/unit/gui/test_heatmap_no_lookahead.py`, including a
   metamorphic case: deleting every bar after the clock must not change
   a single value.
7. **Split-consistency:** tile `size` multiplies price and shares on the
   same split basis. Most vendors back-adjust price history to
   **today's** basis (yfinance does so unconditionally — `auto_adjust=False`
   only disables *dividend* adjustment — so a raw price is simply not
   obtainable), in which case the *shares* leg is lifted to meet it via
   `split_factor_after`, measured from the share count's own
   **observation date** (filings are quarterly; a split can land between
   the last one and the clock). Where the source serves as-reported
   prices instead (Alpaca in `raw` / `dividend` mode, per
   `data.quality.is_split_adjusted`) both legs are already raw and no
   lift is applied. Equivalently, in either basis: a split occurring
   **after** the replay clock must not change any tile's area — the
   sizing analogue of Invariant 6. Getting the basis wrong mis-sizes the
   tile by exactly the cumulative split ratio, in whichever direction
   (measured on a 2020-06-01 replay: NVDA 40×, AMZN/GOOGL 20×, TSLA 15×,
   AAPL 4× — and because a treemap normalises to a unit square, the
   non-splitters inflate to absorb the freed area, so *every* tile is
   wrong). Pinned by `tests/unit/backtest/test_heatmap_split_basis.py`.
8. **No look-ahead membership:** no symbol with `Date added` >
   `as_of_ts` appears in a layout built from `members_asof` output;
   the boundary (`Date added == as_of_ts`) is included.
9. `approx_size` is True exactly for tiles whose symbol was in
   `approx_size_symbols` (carried-back share count); it affects neither
   geometry nor color.

## Data Flow / Algorithm
```text
# per session roll:
sizes   = {sym: scaled_cap(raw_shares_at(sym, session), raw_price[sym]) ...}  # split-consistent
layout  = build_layout(symbols, sizes, classification)
  ├─ group symbols by sector, then industry (Unclassified fallback)
  ├─ squarify sectors within [0,1]², by summed child size
  ├─ squarify industries within each sector rect
  └─ squarify symbols within each industry rect  → tile x/y/w/h

# per bar:
price = price_at_or_before(intraday[sym], clock)          # bar AT the clock
base  = completed_session_closes(daily[sym], clock)[-1]   # last COMPLETED day
pcts  = {sym: compute_1d_pct(price, base) ...}
model = apply_colors(layout, pct_by_symbol=pcts, as_of_ts=clock)
  └─ tile.fill = finviz_hex(pct, clip_pct); tile.pct = pct
```

## Testing
- `tests/unit/backtest/test_heatmap.py` — squarify tiling /
  determinism / no-negative-dims; `compute_1d_pct` `None` on missing /
  zero prior close; `price_at_or_before` no-future-leakage cutoff +
  ms/s normalization + NaN-close skip; `scaled_cap`; sector → industry
  grouping + `Unclassified` fallback; `finviz_hex` bucket boundaries +
  `±clip_pct` clamp; missing-data → neutral; `apply_colors`
  non-mutation; `members_asof` look-ahead exclusion + inclusive boundary
  (`Date added == as_of` stays in); `build_layout` sets `approx_size`
  for flagged symbols only.

## Known limitations / Future work
- Membership is point-in-time via the `Date added` filter
  (`members_asof`), so look-ahead names are removed; names that *left*
  the index before today remain absent (survivorship residual, surfaced
  by a coverage label). Full membership via a Wikipedia changes-log
  reconstruction is v2. `sector` / `industry` (GICS) stay as-of-today.
  Shares are **historical** (SEC EDGAR XBRL, ~2009+); before
  the series starts the caller carries back the earliest known count
  (nearest-in-time) and those tiles are `approx_size` + noted in the
  coverage label. Depth upgrades: SEC EDGAR XBRL (~2009, CIK in
  `tools/sp500.csv`), then a paid provider (decades) — v2/later. See
  [`docs/SANDBOX_HEATMAP.md`](../../../docs/SANDBOX_HEATMAP.md).
- v1 color basis is 1-Day % only. RS / vs-SPY and the owner's pluggable
  custom-RS metric are v2 — accepted via a different `pct_by_symbol`
  map, no API change.
- Additional Finviz timeframes (1W … YTD) are v2 (need trailing daily
  history per symbol).

## Recent history
- **Fixed tile sizing.** `size` multiplied a back-adjusted price by an
  as-reported share count, under-sizing every post-replay-date splitter
  by its cumulative split ratio (NVDA 40× on a 2020 replay) and
  inflating the non-splitters to fill the gap. Added
  `split_factor_after` and rewrote Invariant 7: the old "raw price ×
  raw shares" rule is unachievable (vendors always back-adjust splits),
  and stating it that way is what misled the implementation.
- Added `session_date_of` + `completed_session_closes` and documented
  `price_at_or_before` as intraday-only, closing the daily-bar
  look-ahead leak in the heatmap's 1-Day % base leg (Invariant 6 is now
  enforced in code and pinned by a metamorphic test, not just a
  documented caller contract).
- Pure layer implemented (`heatmap.py`) + `tests/unit/backtest/test_heatmap.py`.
  Adds `price_at_or_before` as the one clock-aware helper and optional
  `timeframe` / `universe_id` on `apply_colors`; layout math is pure
  Python (no numpy). Encodes the eleven v1 decisions (see
  `docs/SANDBOX_HEATMAP.md`).
