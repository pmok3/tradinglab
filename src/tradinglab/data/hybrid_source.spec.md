# data/hybrid_source.py — Spec

Last updated: 2026-09-23

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
  `DataFetcher`. Returns a list-compatible `disk_cache.HistorySnapshot` when
  data is available (including an empty successful recent fetch).
  Sub-fetchers + deep-cache loader/saver are injectable seams for
  offline tests (production defaults: `yfinance_source.fetch_live_data`,
  `alpaca_source.fetch_alpaca_data`, and the `alpaca`-keyed `disk_cache`).
  A saver may return `bool` (`True` confirms persistence; `False` means failure)
  or legacy `None` (unknown, never proof of persistence). Only explicit `True`
  clears durable quarantine. An unknown outcome retains verified memory bars
  and uses the bounded persistence retry path without another network fetch.

## Contract
- **yfinance wins overlaps (the user's quality rule).** On any bar both legs
  have, the yfinance value is kept — so the recent/visible window is pure
  yfinance (full volume AND real-time). Alpaca only contributes the tail
  **older than yfinance's oldest bar**.
- **Warm reuse requires evidence.** With recent bars available, at least five
  unique, finite, positive overlapping closes must all have fresh/cached ratios
  in `[0.8, 1.25]`. Ordinary same-basis vendor drift incurs no Alpaca fetch.
  Missing/expired overlap is *unknown*, not "no restatement": fetch a replacement
  and apply the same check before publishing or persisting it. A candidate with
  too few usable bars, inconsistent prices, or delayed adjustment is withheld.
- **Diagnosis is not corporate-action inference.** `_deep_leg_restated` labels
  only a coherent large rescaling (median outside the band and at least 90%
  of ratios within 5% of that median). Other failures are described as
  insufficient/inconsistent evidence, never silently rescaled or called splits.
  The `[0.8, 1.25]` band is compatibility evidence, not a guarantee of detecting
  every corporate restatement: small changes inside the tolerance can pass.
- **Quarantine reaches derived caches.** On entering quarantine, invalidate the
  exact ticker/interval's hybrid and Auto disk files and in-flight/memory
  snapshots through `disk_cache.invalidate_history`. The suspect Alpaca file is
  retained but not trusted by this source. A durable `.invalid` marker preserves
  quarantine across restart, including when Yahoo is also unavailable. Only a
  verified, successfully saved deep replacement clears the marker.
- **Bounded recovery.** Failed/empty/unverifiable replacements back off for
  60s, 120s, ... up to 1800s (monotonic time). Recent Yahoo fetches continue.
  Per-key recovery state uses `LRUDict(maxsize=128)` and 32 fixed lock stripes
  serialize observations of a key, including through Auto. Restart/LRU eviction
  can cause an early retry, not acceptance of quarantined data. Successfully
  verified bars remain usable in memory if persistence fails, without repeated
  network fetches; persistence retries use the same capped backoff, and restart
  revalidates the unsaved repair.
- **Integrity over depth is visible.** While recovery is pending, publish only
  verified recent bars and attach a warning explaining shorter history and
  retry delay. Chart status and prefetch status surface it. Errors from either
  fetcher and deep cache reads/writes are logged; no bad replacement is saved.
- **Return-value semantics preserve the app's "`None` = failed fetch".** Returns
  the merged list (possibly empty); returns `None` **only** when the yfinance
  leg hard-failed (`None`) AND no usable deep data exists. An Alpaca-only result
  during an ordinary Yahoo outage remains supported, but never while that
  history is quarantined. The chart clears an obsolete view rather than
  falling back to an invalidated snapshot.
- **Ratio pseudo-symbols** (`AMD/NVDA`) short-circuit to the yfinance leg only
  (Alpaca has no ratio concept) — avoids a wasted 404 Alpaca fetch and matches
  yfinance's own ratio behaviour.
- Fetcher/loader/saver failures degrade to recent-only with diagnostics;
  insufficient evidence never authorizes a mixed-basis series.
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
- Deep fetches happen on cold miss or failed revalidation, not every poll.
  Recovery is not declared until the replacement itself passes validation.
- Registered only when `AlpacaCredentials.is_configured()`; if Alpaca is later
  removed, `AppState._resolve_source` demotes a persisted `"yfinance+alpaca"`
  selection to the first user-visible source.

## Testing
`tests/unit/data/test_hybrid_source.py` — `merge_prefer_recent` overlap/empty;
`fetch_hybrid_data` cold-stitch+persist, warm-cache-reuse (no network), recent-
only, deep-only-on-yfinance-fail, `None`-vs-`[]` distinction, ratio short-
circuit, deep-error swallow, name constant. Split/restatement coverage:
`test_split_restatement_invalidates_deep_and_heals_seam` (seeds a pre-split
deep cache, presents restated post-split legs, asserts the deep leg is
refetched + re-saved AND the merged output has no seam cliff via a
max-adjacent-jump continuity assertion); `test_no_restatement_no_refetch`
(same-scale legs with small vendor drift reuse the warm cache — zero extra
fetches); `test_both_orderings_converge_to_same_continuous_series`
(recent-refresh-first vs already-restated-deep-first both converge to the
identical continuous series, and the healed cache stays stable on the next
poll); plus `_deep_leg_restated` detector unit tests (forward/reverse split
flagged, small drift ignored, minimum-overlap and disjoint-timestamp guards).
Additional regressions cover expired overlap, failed/empty/still-unadjusted
replacement, both outer namespaces, bounded retries and recovery, restart
quarantine, write failures, stale worker publication, finite/unique evidence,
and unrelated-key preservation. `tests/unit/gui/test_hybrid_recovery_app.py`
drives real synchronous/asynchronous ChartApp loaders through the Auto delegate.
The legacy `None`/no-op saver regression verifies that rejected Alpaca bytes
cannot be accepted after restart during a Yahoo outage; both `False` and `None`
outcomes retry persistence without refetching, and explicit `True` clears the marker.
Ranking is pinned in
`tests/unit/data/test_source_ranking.py` (`test_hybrid_ranks_just_above_yfinance`);
volume metadata is pinned in `tests/unit/data/test_quality.py`
(`test_hybrid_volume_is_full`).
