# data/hybrid_source.py — Spec

## Purpose
A composite data source that stitches **yfinance (recent + live)** over
**Alpaca (deep history)** into one continuous series, giving a completely-free
user the best of both: yfinance's real-time, full-consolidated-volume recent
window PLUS Alpaca's deep intraday reach (IEX, ~2016+). Registered as
`"yfinance+alpaca"` in `DATA_SOURCES` only when Alpaca is configured.

## Public API
- `HYBRID_SOURCE_NAME = "yfinance+alpaca"` — the registry key (shown verbatim
  in the toolbar source dropdown; there is no display-name layer).
- `merge_prefer_recent(deep, recent) -> list[Candle]` — merge two legs with the
  **recent leg winning every overlapping bar**. Thin wrapper over
  `disk_cache.merge_candles(deep, recent, presorted=True)` ("new wins on
  duplicate date" + keeps both sides' non-overlapping bars).
- `fetch_hybrid_data(ticker, interval, *, recent_fetcher=None, deep_fetcher=None,
  deep_loader=None, deep_saver=None, **_ignored) -> list[Candle] | None` — the
  `DataFetcher`. Sub-fetchers + deep-cache loader/saver are injectable seams for
  offline tests (production defaults: `yfinance_source.fetch_live_data`,
  `alpaca_source.fetch_alpaca_data`, and the `alpaca`-keyed `disk_cache`).

## Contract
- **yfinance wins overlaps (the user's quality rule).** On any bar both legs
  have, the yfinance value is kept — so the recent/visible window is pure
  yfinance (full volume AND real-time). Alpaca only contributes the tail
  **older than yfinance's oldest bar**.
- **Deep-leg disk reuse keeps the live poll cheap.** Alpaca's contribution is
  immutable sealed history, so `_resolve_deep_leg` reuses the on-disk `alpaca`
  cache when present and pays the slow paginated network fetch only on a cold
  miss (then persists it). Each live poll therefore refetches ONLY the yfinance
  leg and reuses the cached tail.
- **Return-value semantics preserve the app's "`None` = failed fetch".** Returns
  the merged list (possibly empty); returns `None` **only** when the yfinance
  leg hard-failed (`None`) AND Alpaca yielded nothing. An Alpaca-only result
  (yfinance down, deep history present) is returned as data, not `None`.
- **Ratio pseudo-symbols** (`AMD/NVDA`) short-circuit to the yfinance leg only
  (Alpaca has no ratio concept) — avoids a wasted 404 Alpaca fetch and matches
  yfinance's own ratio behaviour.
- **Never raises.** Each leg + the loader/saver is wrapped; a failing deep leg
  degrades to recent-only.
- Registered **period-style (no `supports_range`)**: the trailing fetch returns
  the full merged series, so drilldown / prefetch find deep days in it; the
  prefetch scheduler treats it as a period source (band-0 warm, no deepening).

## Design Decisions
- **Composite-as-registered-source**, not a per-call-site role split: it slots
  into every source-parameterised path (load, poll, drilldown, prefetch, UI)
  with no changes to the ~20 `source_var.get()` call sites, and the cache is
  already namespaced by `(source, ticker, interval)` so the merged series has a
  clean `"yfinance+alpaca"` namespace with no collision.
- **Live gating is automatic.** `gui/polling._live_updates_delayed_for_source`
  only suppresses live polling for `source == "alpaca"`; `"yfinance+alpaca"` is
  live-capable because its live edge is the (real-time) yfinance leg. No gate
  change is needed.
- **Ranked by the global priority** (`data/source_ranking.py`): the hybrid sits
  **just above plain `yfinance`** (its live edge is full-volume yfinance plus a
  deeper tail, so it's never worse) and **below the full-volume deep vendors**
  (`alpaca@paid`, `schwab`, `polygon`). A fresh startup selects `"Auto"` by
  default, and Auto will resolve to the hybrid for a free-Alpaca user because it
  outranks plain `yfinance` and raw free-Alpaca while still sitting below the
  full-volume deep vendors.
- **Depth caveat:** the default deep leg is Alpaca's `provider_lookback_days`
  window (e.g. 5m ≈ ~120d), so hybrid roughly DOUBLES yfinance's ~60-day 5m
  reach with full volume on the recent half. Deeper-than-that history is a
  future prefetch-deepening enhancement (would need a `page_fetcher` routing
  band-0 → yfinance, deeper bands → Alpaca).

## Invariants
- Merged output is date-ascending; overlapping dates carry the yfinance bar.
- The deep (Alpaca) leg is fetched from the network at most once per
  `(ticker, interval)` until its disk cache is cleared.
- Registered only when `AlpacaCredentials.is_configured()`; if Alpaca is later
  removed, `AppState._resolve_source` demotes a persisted `"yfinance+alpaca"`
  selection to the first user-visible source.

## Testing
`tests/unit/data/test_hybrid_source.py` — `merge_prefer_recent` overlap/empty;
`fetch_hybrid_data` cold-stitch+persist, warm-cache-reuse (no network), recent-
only, deep-only-on-yfinance-fail, `None`-vs-`[]` distinction, ratio short-
circuit, deep-error swallow, name constant. Ranking is pinned in
`tests/unit/data/test_source_ranking.py` (`test_hybrid_ranks_just_above_yfinance`);
volume metadata is pinned in `tests/unit/data/test_quality.py`
(`test_hybrid_volume_is_full`).
