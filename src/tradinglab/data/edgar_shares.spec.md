# data/edgar_shares.py — Spec

## Purpose
SEC EDGAR XBRL provider for historical shares outstanding — the
`edgar` entry in the [`shares_sources`](shares_sources.spec.md)
registry, and the app's sole share-count source. Reads
`dei:EntityCommonStockSharesOutstanding` from the SEC's free
`data.sec.gov` XBRL API. Feeds market-cap tile sizing in the
[sandbox heatmap](../gui/sandbox_heatmap.spec.md).

## Public API
- `USER_AGENT` — static application identifier sent on every request.
- `UrlFetcher = Callable[[str], Any]` — injected network hook returning
  decoded JSON; raises on failure.
- `parse_company_concept(payload) -> list[SharesFact]` — parse a
  `companyconcept` payload; ascending by `as_of_ts`, one fact per `end`
  date (latest `filed` wins). Never raises.
- `parse_ticker_map(payload) -> dict[str, int]` — parse
  `company_tickers.json` to `{TICKER: cik}`, dot-munged (`BRK.B` →
  `BRK-B`).
- `class EdgarSharesFetcher` — callable `(symbol) -> list[SharesFact]`.
  `__init__(*, url_fetcher=None, cik_lookup=None)`; `cik_for(symbol)`.
- `make_fetcher(*, url_fetcher=None, cik_lookup=None)` — registry entry
  point.
- `prefetch_quarter(period, *, url_fetcher=None, symbols=None) -> {cik: shares}`
  — bulk-load one quarter for every filer in a single request.

## Dependencies
- Internal: [`shares_sources`](shares_sources.spec.md) (`SharesFact`).
- External: `json`, `urllib.request`, `datetime` (stdlib only — no new
  dependency for a network provider).

## Design Decisions
- **Chosen over a price vendor's fundamentals feed on measured
  grounds.** Against yfinance `get_shares_full`: one clean as-reported
  value per filing vs. a series that interleaves as-reported and
  already-split-adjusted values (TSLA carried 0.186B, 0.932B *and*
  4.659B for 2020-08-31, the last being double-adjusted); a `filed`
  date vs. none; ~91-day median cadence and a 98-day worst gap vs. 675
  days; ~10k US filers vs. whatever one vendor carries. It is also the
  authoritative source — it *is* what companies report.
- **Both dates are preserved.** `end` → `as_of_ts` anchors the
  split-basis lift; `filed` → `filed_ts` is what makes point-in-time
  replay possible. Sizing a tile from a count that had not been filed
  yet is look-ahead, and only EDGAR gives us the means to avoid it.
- **Amendments supersede.** A `10-K/A` restates a period already
  reported; keeping one fact per `end` (latest `filed`) means the
  correction replaces the original instead of appearing beside it.
- **Splits are NOT sourced here.** A split is a price-series concern and
  the shares lift must match the basis the *prices* are on, so the split
  calendar stays with the price vendor.
- **Network is injected** (`url_fetcher`), so every parsing rule is
  offline-testable and the SEC is never contacted from the test suite.
- **CIK lookup is injected too.** The heatmap passes the CIK already
  shipped in `tools/sp500.csv`, so the S&P universe pays no network
  ticker resolution — and resolving by CIK is rename- and
  recycled-ticker-safe, which a bare symbol is not. The SEC ticker map
  is the fallback for other universes, fetched once and cached
  in-process.
- **Static, impersonal User-Agent.** SEC requires automated clients to
  identify themselves; we send an application identifier and never
  anything derived from the user's machine, account or credentials.
  SEC also asks for ≤10 requests/second.
- **Every failure yields `[]`.** A fundamentals outage must never raise
  into the render path; empty is indistinguishable from "non-filer",
  which is the honest reading either way.
- **Bulk `frames` endpoint for width.** One request returns ~4,800
  companies for a quarter, so a wide universe costs a handful of calls
  instead of one per symbol.

## Invariants
- `parse_company_concept` returns facts ascending by `as_of_ts`, with
  `filed_ts >= as_of_ts` for well-formed input, and at most one fact per
  `as_of_ts`.
- No function raises on a malformed payload; all degrade to `[]` / `{}`.
- `USER_AGENT` contains no personal information.

## Data Flow / Algorithm
```text
symbol → cik_for()                       # injected lookup, else SEC ticker map
      → GET companyconcept/CIK{cik}/dei/EntityCommonStockSharesOutstanding
      → parse_company_concept
          ├─ skip rows with unparseable end / filed / val, or val <= 0
          ├─ keep the latest `filed` per `end` (amendments supersede)
          └─ sort by `end`
      → list[SharesFact(as_of_ts, filed_ts, shares)]
```

## Testing
- `tests/unit/data/test_edgar_shares.py` — parsing (both dates present,
  ascending, amendment supersedes, malformed payloads, bad rows skipped
  individually); ticker-map parsing + dot-munge; fetcher wiring (injected
  CIK avoids a lookup, ticker-map fallback is cached, unknown symbol,
  network failure degrades); bulk quarter prefetch; User-Agent carries no
  personal data.

## Known limitations / Future work
- **Depth is bounded by the XBRL mandate (~2009).** Replays earlier than
  that fall back to carry-back, flagged approximate.
- **Foreign private issuers** (ADRs) file 20-F annually, so their counts
  stay sparse. A documented residual, not a fixable one.
- `company_tickers.json` lists current registrants only, so a delisted
  symbol resolves only when its CIK is supplied — which is why the
  heatmap passes the shipped CIK.
- `prefetch_quarter` is implemented but not yet wired into the heatmap's
  prime path; per-symbol lookups are used today.

## Recent history
- Added as the sole share-count source, replacing yfinance
  `get_shares_full` after that series was measured to interleave bases,
  duplicate dates, double-apply splits, and omit filing dates.
