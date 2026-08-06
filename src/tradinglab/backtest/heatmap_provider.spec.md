# backtest/heatmap_provider.py — Spec

## Purpose
Classification + membership + historical-shares + split-history provider
feeding the sandbox heatmap. Loads sector / industry / `Date added` / CIK for the
S&P 500 from the shipped `tools/sp500.csv` GICS columns (offline), and
fetches the per-symbol historical shares-outstanding series
(`get_shares_full`) and split history (`Ticker.splits`) from yfinance
(the only network fields), disk-cached. The
window in [`gui/sandbox_heatmap.py`](../gui/sandbox_heatmap.spec.md)
composes these into `size_by_symbol` / membership for the pure
[`heatmap`](heatmap.spec.md) layer. See
[`docs/SANDBOX_HEATMAP.md`](../../../docs/SANDBOX_HEATMAP.md).

## Public API
- `SharesSeries = list[tuple[int, float]]` — ascending `(epoch_seconds, shares)`.
- `SharesFetcher = Callable[[str], SharesSeries]` — injected fetcher type.
- `SplitsSeries = list[tuple[int, float]]` — ascending `(epoch_seconds, ratio)`.
- `SplitsFetcher = Callable[[str], SplitsSeries | None]` — injected fetcher
  type. `[]` = "never split, known"; `None` = fetch failed, basis unknown.
- `parse_date_added(value) -> int | None` — `sp500.csv` `Date added`
  (`YYYY-MM-DD`) → UTC epoch seconds; empty / unparseable → `None`.
- `load_sp500_meta(csv_path=None) -> dict[str, dict]` — parse the CSV to
  `{symbol: {sector, industry, cik, date_added_ts}}`; dot-munges
  `BRK.B` → `BRK-B`. Defaults to the shipped CSV via `resource_path`.
- `shares_at_from_series(series, ts) -> (float | None, bool)` — snap to
  `ts`: exact most-recent ≤ `ts`; **carry back** the earliest known
  count (flagged `True`) when `ts` precedes the series; `(None, True)`
  when empty. ms→s normalized via `core.timezones.normalize_epoch_to_seconds`.
- `shares_at_detail_from_series(series, ts) -> (float | None, bool, int | None)`
  — the same, plus the **observation timestamp** of the returned count.
  Needed because the split factor is measured from the filing date, not
  the replay clock.
- `class HeatmapProvider` (dataclass) — `meta` / `shares_fetcher` /
  `splits_fetcher` / `cache_dir` / `price_split_adjusted`.
  - `symbols()`, `classification() -> {sym: Classification}`,
    `date_added() -> {sym: int | None}`, `cik(sym)`.
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
- Internal: [`heatmap`](heatmap.spec.md) (`Classification`),
  [`core/timezones`](../core/timezones.spec.md) (`normalize_epoch_to_seconds`),
  `.._resources.resource_path`, `..paths.app_data_dir`.
- External: `csv`, `json`, `os`, `datetime` (stdlib); `yfinance` only in
  the default fetcher (imported lazily, failure-tolerant).

## Design Decisions
- **Classification from GICS, not yfinance, for the S&P 500.** The
  shipped `sp500.csv` already carries `GICS Sector` / `GICS Sub-Industry`
  — offline, authoritative, no per-symbol `.info` rate-limit. yfinance
  `.info` stays the fallback for non-S&P universes (v2). This is a
  robustness refinement of decision 2 (still "yfinance, not Finviz
  scraping"), not a reversal.
- **Shares and splits are the only network fields.** `get_shares_full`
  and `Ticker.splits` are fetched lazily per symbol and disk-cached
  (`shares_cache.json` / `splits_cache.json`, atomic `os.replace`), so
  repeat sessions skip the network. A shares fetch failure yields an
  empty series → the window degrades to carry-back / sliver.
- **`[]` and `None` mean different things for splits.** `[]` is "this
  company never split — factor 1.0, known"; `None` is "the lookup
  failed". Collapsing them would make a failed fetch silently reinstate
  the exact under-sizing the correction exists to remove, so `None`
  renders the unlifted count but flags the tile `approx_size`.
- **An unknown split history is never persisted.** `None` is cached
  in-memory for the session (one attempt per run, so a rate-limit storm
  doesn't retry 500 times) but filtered out of `splits_cache.json`, and
  a `null` found on disk from an older build is ignored rather than
  trusted. Persisting it would make the failure permanent: every later
  launch would reload it, skip the fetch, and render the unlifted count
  forever behind nothing louder than a hatched border. `Ticker.splits`
  pulls a full per-symbol history, so a 500-name `prime()` is markedly
  more rate-limit-prone than the old shares-only one — one 429 storm
  must not poison the cache.
- **The lift is conditional on the price basis.** `price_split_adjusted`
  is set by the window from `data.quality.is_split_adjusted(session
  source)`. Alpaca in `raw` / `dividend` adjustment mode serves
  as-reported prices; lifting the shares against those would over-size
  every splitter by exactly the ratio the correction removes — the
  mirror image of the bug, same magnitude. Asserting "every vendor
  back-adjusts" as a universal premise is what this guards against.
- **The split factor is measured from the filing date, not the clock.**
  `get_shares_full` reports roughly quarterly, so a split can land
  between the last filing and the replay date; using the clock would
  miss it and leave the count a whole ratio short. Hence
  `shares_at_detail_from_series` surfaces the observation timestamp.
- **Injected fetchers.** `shares_fetcher` / `splits_fetcher` default to
  the yfinance wrappers but are swappable, so the provider is fully
  offline-testable (and the test suite injects both — an omitted
  `splits_fetcher` would silently put unit tests on the network).
- **Carry-back lives here** (spec §Known limitations of `heatmap`).
  `shares_at_from_series` returns the approx flag the window forwards to
  `build_layout(approx_size_symbols=…)`.
- **Pure helpers split from I/O.** `parse_date_added` /
  `load_sp500_meta` / `shares_at_from_series` are pure and unit-tested;
  disk-cache + network are best-effort and swallow errors.

## Invariants
1. `shares_at_from_series` never returns a count from a point after `ts`;
   before the series start it carries back the earliest known count with
   `approx=True`.
2. `load_sp500_meta` munges dots so symbols match yfinance form.
3. Disk-cache read / write failures are swallowed — the provider always
   returns usable (possibly empty) data.
4. `basis_shares_at` never returns a count lifted by a split dated at or
   before that count's observation date, and reports `approx=True`
   whenever the split history is unknown (see `heatmap` Invariant 7).

## Testing
- `tests/unit/backtest/test_heatmap_provider.py` — `parse_date_added`
  valid / empty / malformed; `load_sp500_meta` on a temp CSV
  (sector / industry / cik / date munge); `shares_at_from_series` exact
  snap, carry-back approx flag, empty; `HeatmapProvider` with a fake
  fetcher + `tmp_path` cache (classification / date_added / shares_at,
  disk round-trip).
- `tests/unit/backtest/test_heatmap_split_basis.py` — `split_factor_after`
  windowing / boundary / fractional / garbage / ms inputs;
  `shares_at_detail_from_series` observation timestamps; the factor is
  taken from the filing not the clock; unknown history flags approximate
  while `[]` stays exact; the governing property that a split after the replay clock leaves tile
  area unchanged; a failed fetch is never persisted and recovers on the
  next launch; the lift is skipped when the price series is not
  split-adjusted; and real 2020 AAPL / NVDA / AMZN / MSFT caps.

## Known limitations / Future work
- S&P 500 only (matches the heatmap v1 universe). Non-S&P classification
  via yfinance `.info` is v2.
- Shares depth ~11y (`get_shares_full`); deeper history via SEC EDGAR
  XBRL (CIK captured here) / a paid provider is v2/later.

## Recent history
- Added the split-history series (`Ticker.splits`, `splits_cache.json`)
  and `basis_shares_at`, which lifts an as-reported share count onto the
  price series' back-adjusted basis. Without it, tile area multiplied a
  back-adjusted price by an as-reported count and under-sized every
  post-replay-date splitter by its cumulative ratio.
- Implemented alongside the pure `heatmap` layer. Sources GICS from
  `sp500.csv` (offline) + shares from yfinance `get_shares_full`.
