# backtest/heatmap_provider.py — Spec

## Purpose
Classification + membership + shares/split provider feeding the sandbox
heatmap. Loads sector / industry / `Date added` / CIK for the
S&P 500 from the shipped `tools/sp500.csv` GICS columns (offline), and
takes the per-symbol shares-outstanding history from an **injected**
provider (the `shares_data_source` tunable, resolved by a higher-level
caller — this module never imports a vendor) and the split history from
the price vendor. Both are disk-cached. The
window in [`gui/sandbox_heatmap.py`](../gui/sandbox_heatmap.spec.md)
composes these into `size_by_symbol` / membership for the pure
[`heatmap`](heatmap.spec.md) layer. See
[`docs/SANDBOX_HEATMAP.md`](../../../docs/SANDBOX_HEATMAP.md).

## Public API
- `SharesSeries = list[SharesFact]` — ascending by `as_of_ts`. `SharesFact` and `SharesFetcher` are re-exported from [`data/shares_sources`](../data/shares_sources.spec.md).
- `SplitsSeries = list[tuple[int, float]]` — ascending `(epoch_seconds, ratio)`.
- `SplitsFetcher = Callable[[str], SplitsSeries | None]` — injected fetcher
  type. `[]` = "never split, known"; `None` = fetch failed, basis unknown.
- `parse_date_added(value) -> int | None` — `sp500.csv` `Date added`
  (`YYYY-MM-DD`) → UTC epoch seconds; empty / unparseable → `None`.
- `load_sp500_meta(csv_path=None) -> dict[str, dict]` — parse the CSV to
  `{symbol: {sector, industry, cik, date_added_ts}}`; dot-munges
  `BRK.B` → `BRK-B`. Defaults to the shipped CSV via `resource_path`.
- `shares_at_from_series(series, ts) -> (float | None, bool)` —
  **point-in-time** snap: only facts already `filed` at `ts` are
  eligible, and among those the most recently *reported* wins (greatest
  `as_of_ts`). Nothing filed yet → **carry back** the earliest known
  count (flagged `True`); `(None, True)` when empty. ms→s normalized via
  `core.timezones.normalize_epoch_to_seconds`.
- `shares_at_detail_from_series(series, ts) -> (float | None, bool, int | None)`
  — the same, plus the selected fact's **`as_of_ts`**. Needed because the
  split factor is measured from the date the count describes, not the
  replay clock.
- `class HeatmapProvider` (dataclass) — `meta` / `shares_fetcher` /
  `splits_fetcher` / `cache_dir` / `price_split_adjusted`.
  - `symbols()`, `classification() -> {sym: Classification}`,
    `date_added() -> {sym: int | None}`, `cik(sym)`, `cik_int(sym)`.
  - `shares_series(sym)` — lazy fetch + disk-cache; `shares_at(sym, ts)`
    delegates to `shares_at_from_series`; `peek_shares_at(sym, ts)` is
    cache-only and never fetches; `prime(symbols=None)` pre-fetches
    **both** series.
  - `splits_series(sym) -> SplitsSeries | None` / `peek_splits_series(sym)`
    — lazy fetch + disk-cache / cache-only.
  - `basis_shares_at(sym, ts) -> (float | None, bool)` — the share count
    expressed on **the price series'** basis: when
    `price_split_adjusted` (the default), the as-reported count × splits
    after its observation date; when False (e.g. Alpaca in `raw` /
    `dividend` mode, where prices are as-reported too) no lift is
    applied. This is what the heatmap multiplies by price;
    `peek_basis_shares_at` is the non-blocking form. `approx` is True
    when the count was carried back **or** the split history is unknown.

## Dependencies
- Internal: [`heatmap`](heatmap.spec.md) (`Classification`,
  `split_factor_after`), [`data/shares_sources`](../data/shares_sources.spec.md)
  (`SharesFact`, `null_shares_fetcher`),
  [`core/timezones`](../core/timezones.spec.md) (`normalize_epoch_to_seconds`),
  `.._resources.resource_path`, `..paths.app_data_dir`.
- External: `csv`, `json`, `os`, `datetime` (stdlib); `yfinance` only in
  the default **splits** fetcher (imported lazily, failure-tolerant).
  The shares provider is injected and imports nothing here.

## Design Decisions
- **Classification from GICS, not yfinance, for the S&P 500.** The
  shipped `sp500.csv` already carries `GICS Sector` / `GICS Sub-Industry`
  — offline, authoritative, no per-symbol `.info` rate-limit. yfinance
  `.info` stays the fallback for non-S&P universes (v2). This is a
  robustness refinement of decision 2 (still "yfinance, not Finviz
  scraping"), not a reversal.
- **The shares provider is injected; this module names no vendor.** The
  `shares_data_source` tunable is resolved by a higher-level caller
  (`gui/sandbox_heatmap._build_provider`) via
  `data.shares_sources.resolve_shares_fetcher`, and the result is
  assigned onto `shares_fetcher`. The dataclass default is
  `null_shares_fetcher` — knows nothing, touches no network — so a
  caller that forgets to inject gets "sizes unavailable" rather than
  silent traffic to a vendor nobody selected (which is also how the unit
  suite stays offline by construction).
- **Shares are point-in-time via `filed`, not `as_of`.** A count
  describes a date but becomes public ~2 weeks later; selecting on the
  as-of date alone sizes a replay tile from a number nobody had yet.
  This is why the provider needs `SharesFact`, and why a source without
  a filing date cannot be point-in-time correct.
- **Neither cache persists an unknown.** `shares_cache.json` drops empty
  series and `splits_cache.json` drops `None`; a `null` found on disk
  from an older build is ignored rather than trusted. An empty result is
  indistinguishable from "fetch failed" or "no provider configured", so
  writing it would make the failure permanent — every later launch would
  reload it, skip the fetch, and size the tile at zero (or unlifted)
  forever behind nothing louder than a hatched border. Both cost one
  request per session to retry instead. `Ticker.splits` pulls a full
  per-symbol history, so a 500-name `prime()` is markedly rate-limit-prone
  — one 429 storm must not poison the cache.
- **`[]` and `None` mean different things for splits.** `[]` is "this
  company never split — factor 1.0, known"; `None` is "the lookup
  failed". Collapsing them would make a failed fetch silently reinstate
  the exact under-sizing the correction exists to remove, so `None`
  renders the unlifted count but flags the tile `approx_size`.
- **The lift is conditional on the price basis.** `price_split_adjusted`
  is set by the window from `data.quality.is_split_adjusted(session
  source)`. Alpaca in `raw` / `dividend` adjustment mode serves
  as-reported prices; lifting the shares against those would over-size
  every splitter by exactly the ratio the correction removes — the
  mirror image of the bug, same magnitude. Asserting "every vendor
  back-adjusts" as a universal premise is what this guards against.
- **The split factor is measured from the count's own as-of date, not
  the clock.** Filings are roughly quarterly, so a split can land
  between the last one and the replay date; using the clock would miss
  it and leave the count a whole ratio short. Hence
  `shares_at_detail_from_series` surfaces the as-of timestamp.
- **The shipped CIK resolves the filer.** `cik_int` feeds the shares
  provider's symbol→filer lookup, so the S&P universe never pays a
  network ticker resolution, and a recycled ticker can't map to the
  wrong company.
- **Carry-back lives here** (spec §Known limitations of `heatmap`).
  `shares_at_from_series` returns the approx flag the window forwards to
  `build_layout(approx_size_symbols=…)`.
- **Pure helpers split from I/O.** `parse_date_added` /
  `load_sp500_meta` / `shares_at_from_series` are pure and unit-tested;
  disk-cache + network are best-effort and swallow errors.

## Invariants
1. `shares_at_from_series` never returns a fact that had not been
   **filed** by `ts`; when nothing had, it carries back the earliest
   known count with `approx=True`.
2. `load_sp500_meta` munges dots so symbols match yfinance form.
3. Disk-cache read / write failures are swallowed — the provider always
   returns usable (possibly empty) data, and never persists an unknown
   (empty shares series / `None` splits).
4. `basis_shares_at` never returns a count lifted by a split dated at or
   before that count's as-of date, and reports `approx=True` whenever
   the split history is unknown (see `heatmap` Invariant 7).

## Testing
- `tests/unit/backtest/test_heatmap_provider.py` — `parse_date_added`
  valid / empty / malformed; `load_sp500_meta` on a temp CSV
  (sector / industry / cik / date munge); `shares_at_from_series`
  point-in-time snap (a count is not visible before it is filed),
  carry-back approx flag, empty; `cik_int`; `HeatmapProvider` with a
  fake fetcher + `tmp_path` cache (classification / date_added /
  shares_at, disk round-trip).
- `tests/unit/backtest/test_heatmap_split_basis.py` — `split_factor_after`
  windowing / boundary / fractional / garbage / ms inputs;
  `shares_at_detail_from_series` as-of timestamps; the factor is
  taken from the filing not the clock; unknown history flags approximate
  while `[]` stays exact; the governing property that a split after the replay clock leaves tile
  area unchanged; a failed fetch is never persisted and recovers on the
  next launch; the lift is skipped when the price series is not
  split-adjusted; and real 2020 AAPL / NVDA / AMZN / MSFT caps.
- `tests/unit/data/test_edgar_shares.py` — the provider behind the
  `edgar` registration.

## Known limitations / Future work
- S&P 500 only (matches the heatmap v1 universe). Non-S&P classification
  via yfinance `.info` is v2.
- Shares depth is bounded by the provider — for `edgar`, the XBRL
  mandate (~2009). Earlier replays fall back to carry-back, flagged.
- Filing cadence (~quarterly) is the residual sizing error: mid-quarter
  the count is stale by whatever the company issued or bought back.
  Measured mean error is 0.15–1% for large caps and ~5% for a serial
  diluter; extrapolating the drift was tested and is **worse** than
  carry-forward (share counts behave like a random walk), and
  interpolating toward the *next* filing would be look-ahead. Surfacing
  staleness (age × drift → `approx_size`) is the open item, not a
  better estimator.

## Recent history
- Shares moved to an **injected** provider selected by the
  `shares_data_source` tunable (resolving to SEC EDGAR), and the series
  became `SharesFact` so selection can be point-in-time on the filing
  date. The previous vendor feed interleaved bases, duplicated dates,
  double-applied splits and had no filing date — a 26× TSLA mis-size on
  a post-split replay date.
- Added the split-history series (`Ticker.splits`, `splits_cache.json`)
  and `basis_shares_at`, which lifts an as-reported share count onto the
  price series' back-adjusted basis. Without it, tile area multiplied a
  back-adjusted price by an as-reported count and under-sized every
  post-replay-date splitter by its cumulative ratio.
- Implemented alongside the pure `heatmap` layer. Sources GICS from
  `sp500.csv` (offline).
