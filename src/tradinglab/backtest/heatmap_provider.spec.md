# backtest/heatmap_provider.py — Spec

## Purpose
Classification + membership + historical-shares provider feeding the
sandbox heatmap. Loads sector / industry / `Date added` / CIK for the
S&P 500 from the shipped `tools/sp500.csv` GICS columns (offline), and
fetches the per-symbol historical shares-outstanding series from
yfinance `get_shares_full` (the only network field), disk-cached. The
window in [`gui/sandbox_heatmap.py`](../gui/sandbox_heatmap.spec.md)
composes these into `size_by_symbol` / membership for the pure
[`heatmap`](heatmap.spec.md) layer. See
[`docs/SANDBOX_HEATMAP.md`](../../../docs/SANDBOX_HEATMAP.md).

## Public API
- `SharesSeries = list[tuple[int, float]]` — ascending `(epoch_seconds, shares)`.
- `SharesFetcher = Callable[[str], SharesSeries]` — injected fetcher type.
- `parse_date_added(value) -> int | None` — `sp500.csv` `Date added`
  (`YYYY-MM-DD`) → UTC epoch seconds; empty / unparseable → `None`.
- `load_sp500_meta(csv_path=None) -> dict[str, dict]` — parse the CSV to
  `{symbol: {sector, industry, cik, date_added_ts}}`; dot-munges
  `BRK.B` → `BRK-B`. Defaults to the shipped CSV via `resource_path`.
- `shares_at_from_series(series, ts) -> (float | None, bool)` — snap to
  `ts`: exact most-recent ≤ `ts`; **carry back** the earliest known
  count (flagged `True`) when `ts` precedes the series; `(None, True)`
  when empty. ms→s normalized.
- `class HeatmapProvider` (dataclass) — `meta` / `shares_fetcher` /
  `cache_dir`.
  - `symbols()`, `classification() -> {sym: Classification}`,
    `date_added() -> {sym: int | None}`, `cik(sym)`.
  - `shares_series(sym)` — lazy fetch + disk-cache; `shares_at(sym, ts)`
    delegates to `shares_at_from_series`; `peek_shares_at(sym, ts)` is
    cache-only and never fetches; `prime(symbols=None)` pre-fetches.

## Dependencies
- Internal: [`heatmap`](heatmap.spec.md) (`Classification`),
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
- **Shares are the only network field.** `get_shares_full` is fetched
  lazily per symbol and disk-cached (`shares_cache.json`, atomic
  `os.replace`), so repeat sessions skip the network. Any fetch failure
  yields an empty series → the window degrades to carry-back / sliver.
- **Injected fetcher.** `shares_fetcher` defaults to the yfinance
  wrapper but is swappable, so the provider is fully offline-testable.
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

## Testing
- `tests/unit/backtest/test_heatmap_provider.py` — `parse_date_added`
  valid / empty / malformed; `load_sp500_meta` on a temp CSV
  (sector / industry / cik / date munge); `shares_at_from_series` exact
  snap, carry-back approx flag, empty; `HeatmapProvider` with a fake
  fetcher + `tmp_path` cache (classification / date_added / shares_at,
  disk round-trip).

## Known limitations / Future work
- S&P 500 only (matches the heatmap v1 universe). Non-S&P classification
  via yfinance `.info` is v2.
- Shares depth ~11y (`get_shares_full`); deeper history via SEC EDGAR
  XBRL (CIK captured here) / a paid provider is v2/later.

## Recent history
- Implemented alongside the pure `heatmap` layer. Sources GICS from
  `sp500.csv` (offline) + shares from yfinance `get_shares_full`.
