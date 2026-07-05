# Targeted (on-demand) intraday fetch — design

**Status:** design locked (council + owner consultation 2026-07-04). Not yet
implemented. This doc is the contract; the colocated `.spec.md`s for the
touched/new modules are the per-module detail.

## 1. Problem

Each data source fetches a **fixed trailing window** of bars anchored to
*now* (`fetcher(ticker, interval)` → `start = now − provider_lookback_days`,
`end = now`; see [`constants.provider_lookback_days`](../src/tradinglab/constants.spec.md)).
To reach the deep intraday history a provider actually holds (Alpaca free/IEX:
5m back ~2y, 1m back ~2016) the up-front fetch is huge — 2 years of 5m ≈ 40k
bars ≈ 4 API pages ≈ ~15s — which hung the load and blew the drill-down
deadline. The current stopgap **caps** each intraday window to ~1 API page
(5m ≈ 120d, ~3s) so loads stay fast, but that **loses access** to the older
intraday data. Owner requirement: *"use all the data it provides."*

## 2. Goal

Let the user reach **any** provider-available intraday day **without** a slow
up-front bulk load — by fetching **only the range being viewed, on demand**,
anchored on where the user is looking rather than on *now*.

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | v1 scope | **Drill-down-only targeted fetch.** Keep the fast capped up-front load; pan-back lazy-loading deferred. |
| 2 | Compare symbol | **Yes** — a drill-down also fetches the active compare symbol's matching range (RS/RW alignment), in parallel with the primary. |
| 3 | Fetch size | **Fill ~1 API page.** Round-trip time is dominated by the number of *pages* (each is one HTTP call), not the bar count within a page — so size the window to ~1 real page. **Empirically the free Alpaca IEX feed returns ~2,000 bars/page at ~0.6s/page** (NOT the advertised 10k `limit`); see §4.2. |
| 4 | Window anchor | **Centered** on the clicked day, **boundary-aware**: when the clicked day is near the provider's data-start (or the live end), shift the window so the page fills with *real* bars instead of wasting half on an empty side. |
| 5 | Coverage | **Minimal per-`(source,ticker,interval)` coverage record** — fetched range segments + a discovered data-start watermark. Enables boundary anchoring, skip-if-cached, and telling *loading* apart from *no-data* apart from *provider-exhausted*. |
| 6 | Trigger UX | **Auto-fetch** on a drill-down miss (wait-cursor + status, chart stays interactive); no confirm prompt. |
| — | Providers | **Alpaca validated first.** Polygon likely parity; yfinance intraday degrades with a clear "not available on this provider" message; local/synthetic filter in-memory. |

## 4. Architecture

### 4.1 Fetcher interface extension

Extend the type (backward-compatible; defaults reproduce today's behavior):

```python
# data/base.py
DataFetcher = Callable[..., list[Candle] | None]
#   fetcher(ticker, interval)                          -> trailing window (today)
#   fetcher(ticker, interval, *, start=None, end=None) -> explicit range (new)
```

- The ~20 existing call sites keep calling `fetcher(ticker, interval)` — **unchanged**.
- New targeted paths call through a single helper
  `data.range_fetch.fetch_range(source, ticker, interval, start, end)` which:
  1. checks a **range-capability registry** (`register_source(..., supports_range=True)`);
  2. if capable → calls `fetcher(ticker, interval, start=…, end=…)`;
  3. if not → falls back gracefully (see matrix) and reports *unavailable*.
- **Alpaca / Polygon**: real range support (they already take `start`/`end`).
- **yfinance**: `yf.history()` accepts `start`/`end`, but the **server** still
  caps intraday to ~60 days → advertise `supports_range=True` but return a
  clear *provider-limit* signal when the requested range is outside the cap.
- **ratio**: range-fetch its two legs.
- **local / synthetic**: no network — filter the in-memory series to the range
  (or report *no range* and let the caller use what's loaded).

`provider_lookback_days` **stays** as the *initial interactive window* policy
(fast startup / interval switch). Targeted fetch is additive.

### 4.2 Page-span window sizing

Round-trip time is dominated by the **number of pages** (each page is one HTTP
round trip), not the bar count within a page — so size the window to fill ~1
page. A helper (in `constants.py`) converts the page size to a calendar span
per interval:

```
bars_per_page = 2_000  (Alpaca IEX, empirical)   # see below
span_days(interval) ≈ bars_per_page / bars_per_rth_day(interval)  (× calendar/trading slack)
```

Per interval (Alpaca IEX, ~1 page): **5m ≈ 35 d · 1m ≈ 7 d · 15m ≈ 107 d · 1h ≈ 466 d**.

> **Empirical page size (verified, not the advertised 10k).** Alpaca's docs
> advertise a 10,000-bar `limit`, but the free-tier **IEX** historical feed
> caps each response at **~2,000 bars** regardless of `limit`. Round-trip time
> is near-flat within a page (79 bars = 0.32s, 1,732 bars = 0.64s — mostly
> fixed overhead) and **linear in pages** (~0.6s each). The original design
> assumed 10k/page, which made the 5m window ~179 days = 5 pages ≈ 3s, and a
> compare drilldown fetched that **twice, sequentially** → the ~10s hang users
> reported. Fix: `DEFAULT_BARS_PER_PAGE = 2_000` (1-page window, ~0.6s/symbol)
> **and** the drilldown fetches primary + compare **in parallel** (decision 2)
> via a local 2-worker pool — measured 1.20s → 0.60s. Net drilldown fetch:
> ~10s → ~0.6s. We still send `limit=10000` on each request so a paid SIP feed
> (bigger real pages) benefits automatically. **Rate limit: 200 calls/MINUTE**
> on the free plan (429 on exceed) — a 1-page-per-symbol drilldown (~2 calls)
> is far under budget.

**Anchoring** (`[start, end)` around clicked day `D`, page span `P`):
1. Default **centered**: `start = D − P/2`, `end = D + P/2`.
2. Clamp `end` to `now` (can't fetch the future). If truncated, extend `start`
   backward to refill the page.
3. If `start < data_start` (from the coverage watermark, when known): set
   `start = data_start` and extend `end` forward to refill the page.
4. Never exceed 1 page; if both boundaries clamp, accept a short page.

### 4.3 Coverage record (`data/coverage.py`, new)

A small **sidecar JSON** next to each `disk_cache` JSONL, keyed by
`(source, ticker, interval)`:

```json
{ "version": 1,
  "data_start_ts": 1593993600,        // discovered earliest bar the provider has (or null)
  "exhausted_start": true,            // a fetch asked older than data_start and got nothing
  "segments": [[start_ts, end_ts], …] // merged, sorted [start,end) ranges actually fetched
}
```

API (skeleton — see `data/coverage.spec.md`):
- `load(source, ticker, interval) -> CoverageRecord`
- `record_fetch(source, ticker, interval, req_start, req_end, returned_start, returned_end)`
  — merge the fetched span; if `returned_start > req_start` by a margin, learn
  the `data_start_ts` watermark + set `exhausted_start`.
- `missing_ranges(rec, start, end) -> list[(s,e)]` — the sub-ranges NOT yet covered.
- `covered(rec, start, end) -> bool`
- `data_start(rec) -> int | None`

**Bootstrap:** an existing JSONL cache with no sidecar → treat as
"data present, coverage unknown" (one segment spanning its min/max bar ts),
so we never re-fetch what's already on disk.

**Distinguish the three states** for the UI:
- *loading*: requested range ∈ `missing_ranges` and a fetch is in flight.
- *no bars for range*: fetched, merged, but the provider returned an empty
  interior gap (halt / holiday) — segment covers it, no bars present.
- *provider-exhausted*: request older than `data_start_ts` with `exhausted_start`.

### 4.4 Drill-down targeted-fetch flow

`gui/drilldown.py::_zoom_5m_for_date(day)` (and the sync-fetch fallback):

1. Compute the page-span window `[start, end)` around `day` (§4.2).
2. If `coverage.covered(primary, start, end)` **and** `day`'s bars are cached →
   drill immediately (unchanged fast path).
3. Else → **auto-fetch** (wait-cursor + `"Fetching AMD 5m around 2024-03-12…"`):
   - submit `range_fetch(primary, start, end)` **and**, if a compare symbol is
     active, `range_fetch(compare, start, end)` — **in parallel** on the executor.
   - on completion (marshalled to Tk via the worker-inbox pattern):
     `disk_cache.merge` + `coverage.record_fetch` for each; then drill to `day`.
   - keeps the existing 8s UI deadline (a 1-page parallel primary+compare
     fetch is ~0.6s on Alpaca IEX; the deadline only trips on an unusually
     slow network) → "taking longer than expected," not a scary error.
4. If the provider reports the day is beyond its history (`exhausted_start`) →
   the *provider-limit* status, no fetch.

`_day_within_intraday_fetch_window` becomes: reachable iff `day ≥ data_start`
(or unknown) — no longer pinned to the initial 120d window.

### 4.5 Compare-symbol alignment

RS/RW compares the primary to SPY / a sector ETF. When a compare symbol is
active, every targeted fetch pulls the **same range** for it, so the relative
line is never truncated/misaligned. Sector-ETF auto-fetch is deferred (v2).

### 4.6 Provider behavior matrix

| Source | Range fetch | On out-of-range request |
|---|---|---|
| alpaca | native (`start`/`end`, IEX) | provider-limit (SIP→403 already handled) |
| polygon | native | provider-limit |
| yfinance | `yf.history(start,end)` but ~60d intraday server cap | provider-limit message |
| ratio | range-fetch legs | as legs |
| local / BYOD | filter in-memory | edge-of-data |
| synthetic | deterministic filter | test-only |
| schwab | deferred (not wired) | — |

### 4.7 UX states (see UI/UX council notes)

- Drill-down: wait-cursor + status immediately; on-canvas pill if >~400ms;
  keep chart interactive; 8s → "still fetching," reserve ERROR for true failure.
- Distinct visuals for **loading** (cool band + dots), **no bars for range**
  (thin hatched gap label), **provider history limit** (solid boundary line,
  "Start of Alpaca 5m history"). Never share one treatment.
- `Reset View` still returns to the latest ~200 bars.

## 5. Phasing

- **Phase 1 (v1 — this design):** drill-down targeted range fetch (primary +
  compare), page-span centered/boundary-aware window, minimal coverage record,
  Alpaca-validated, three UX states, 8s deadline. **80/20 win.**
- **Phase 2 (deferred):** explicit "Load visible range" action; sector-ETF
  auto-fetch alongside SPY.
- **Phase 3 (deferred):** pan-back lazy backfill (debounce, coalesce, non-jumpy
  splice) — high UX value, high risk (view-jump, fetch-storms).
- **Phase 4 (deferred):** full coverage index (empty/failed segments, provider
  caps) + provider-wide range semantics.

## 6. Blast radius (files)

- **New:** `data/coverage.py` (+ spec), `data/range_fetch.py` (+ spec) *(may
  fold the helper into `data/base.py`)*.
- **Modified:** `data/base.py` (`DataFetcher` type + `supports_range`),
  `data/alpaca_source.py` / `polygon_source.py` / `yfinance_source.py` (accept
  `start`/`end`), `constants.py` (page-span helper), `disk_cache.py` (merge
  already exists; coverage hook), `gui/drilldown.py` (targeted path + status),
  `app.py` (drill-down wiring). The ~20 trailing-window call sites stay unchanged.

## 7. Risks & guardrails

| Risk | Guardrail |
|---|---|
| False gaps (unfetched looks like no-data) | coverage record distinguishes the 3 states; bootstrap existing JSONL as "present". |
| Fetch storms | v1 has **no** pan-triggered fetch; one in-flight request per `(source,ticker,interval,range)` bucket; coalesce. |
| View jump on cache splice | drill zooms to `day` by timestamp, not index; preserve the visible anchor. |
| Regression across ~20 call sites / 5.5k tests | interface extension is **backward-compatible** (kwargs default to trailing window); targeted path is narrow. |
| Coverage-file corruption | best-effort load → treat as "unknown"; never crash a fetch. |
| Sandbox/replay contamination | v1 only touches live drill-down chart loading, not replay mechanics. |

## 8. Test plan

- Unit: page-span sizing (per interval, boundary clamps), coverage
  `record_fetch`/`missing_ranges`/`covered`/watermark, range-capability
  fallback, provider-limit signalling.
- Integration/offline: `range_fetch` merges into `disk_cache` + updates
  coverage; existing trailing-window behavior unchanged.
- Smoke: drill into an 18-month-old Alpaca 5m day loads (~0.6s: 1 page/symbol,
  primary+compare in parallel — comfortably under the 8s deadline) with the
  compare symbol aligned; a beyond-history day shows the provider-limit status,
  no fetch; no duplicate requests from one action; existing capped startup
  unchanged.

## 9. Success metrics

- Drill-down into an 18-month-old Alpaca 5m day succeeds, ~1 page/symbol,
  ~0.6s (primary + compare fetched in parallel; was ~10s before the
  page-size right-size + parallel-fetch).
- Compare (SPY) range matches the primary — no truncated RS line.
- Existing startup speed unchanged; no false empty chart; no repeat-fetch loop.
- Full unit + smoke suites stay green around drill-down / cache / data-load /
  sandbox.
