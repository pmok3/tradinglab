# data/__init__.py — Spec

## Purpose
Aggregates the data-source plugins (yfinance, synthetic, synthetic-stream bootstrap) into a single importable registry. Also re-exports the normalization helpers (`candles_from_dataframe`, `CandleArrays`, `stash_arrays`/`pop_prebuilt_arrays`) and the parallel-fetch primitive, all at `tradinglab.data.*` for backward compatibility with the flat pre-split layout (`tradinglab.data_sources`).

## Public API
- `DATA_SOURCES: Dict[str, DataFetcher]` — the registry, re-exported from `.base`. Holds EVERY registered source including internal ones (synthetic / synthetic-stream). Smoke tests and sandbox replay dispatch through it directly.
- `DataController`, `FetchService` — controller/service classes re-exported from `.controller` and `.fetch_service`.
- `user_visible_sources() -> list[str]` — re-exported from `.base`; the subset of `DATA_SOURCES` keys safe to show in user UI surfaces (toolbar combobox, Settings → Startup parameters dropdown). Excludes synthetic / synthetic-stream (registered with `internal=True`). First entry remains the default.
- `is_internal_source(name) -> bool` — re-exported from `.base`; True for `"synthetic"` and `"synthetic-stream"`.
- `DataFetcher` — type alias `Callable[..., Optional[List[Candle]]]`; range-capable sources also accept kw-only `start` / `end`.
- `register_source(name, fetcher, *, internal=False, supports_range=False, page_fetcher=None)` — imperative registration. Pass `internal=True` to keep the source out of every user-facing dropdown; `supports_range=True` for fetchers that accept kw-only `start` / `end`; `page_fetcher` for a `(ticker, interval, *, end, limit)` one-request page callable (the prefetch scheduler's deepening primitive).
- `source_supports_range(name) -> bool` and `fetch_range(source, ticker, interval, start_ts, end_ts) -> (Optional[List[Candle]], status)` — targeted range-fetch helpers re-exported from `.base`.
- `source_supports_page(name) -> bool`, `fetch_page(source, ticker, interval, *, end_ts=None, limit=10_000) -> FetchPageResult`, and `FetchPageResult` — the newest-`limit`-bars-before-`end` page primitive re-exported from `.base` (Alpaca registers `fetch_alpaca_page`).
- `fetch_live_data` (yfinance), `fetch_synthetic_data`, `fetch_synthetic_stream_bootstrap` — the three deterministically-registered built-in fetchers. **Synthetic sources are registered with `internal=True`** so the end user never sees an option meant for offline testing / sandbox replay.
- `fetch_schwab_data`, `fetch_alpaca_data`, `fetch_polygon_data` — vendor adapters. `"alpaca"` and `"polygon"` are registered when their respective credentials are available, else inert. **`"schwab"` is currently NOT registered** even when credentials are configured — `schwab_source._http_get_pricehistory` is still a `NotImplementedError` stub, so the registration line in `data/__init__.py` is commented out to keep a broken option out of the source-selector dropdown. Re-enable once the REST GET lands. Each adapter builds requests against the vendor's REST endpoint and routes the response through `candles_from_json_rows` for normalization.
- `candles_from_schwab_response`, `candles_from_alpaca_response`, `candles_from_polygon_response` — vendor-specific response-shape adapters that call `candles_from_json_rows` and return `List[Candle]`. Exported for unit-testing parity with the live adapters.
- `candles_from_dataframe`, `candles_from_json_rows`, `CandleArrays`, `pop_prebuilt_arrays`, `stash_arrays` — normalization surface (see `data/normalize.spec.md`).
- `fetch_chunks_parallel` — I/O-parallel fetch helper (for providers that expose chunked APIs).
- `Credentials`, `SchwabCredentials`, `AlpacaCredentials`, `PolygonCredentials`, `get_credentials` — re-exported from `.credentials` (see `data/credentials.spec.md`).
- `register_local_sources() -> list[str]` — reads the `local_data`
  setting and registers every BYOD subsource (`<root_name>-<subdir>`).
  Idempotent: clears the disk-cache no-persist set first, then
  re-marks every registered key. Returns the list of registered source
  keys (empty when BYOD is disabled or the setting is malformed).
  Called once at import time and again whenever the user clicks Save
  in the Configure Local Data dialog.
- `make_local_fetcher`, `discover_subsources` — re-exported from
  `.local_source` (see `data/local_source.spec.md`).
- `RATIO_DELIMITER`, `parse_ratio_symbol`, `is_ratio_symbol`, `canonical_ratio_symbol`, `ratio_display_label`, `compute_ratio_candles`, `fetch_ratio` — re-exported from `.ratio_source` (see `data/ratio_source.spec.md`). Ratio pseudo-symbols use the general `NUM/DEN` form only (e.g. `AMD/NVDA`). NOT a `DATA_SOURCES` entry — resolution is hooked into `fetch_live_data`, so a ratio symbol is a typed *ticker*, not a selectable *source*. Never persisted to disk (see `disk_cache.spec.md`).

## Dependencies
- Internal: all data submodules.
- External: transitive (numpy, yfinance-lazy).

## Design Decisions
- **Registration order matters**: `yfinance` registers first so the UI's default source combobox selection keys off it (`next(iter(DATA_SOURCES))`). Order: yfinance → synthetic → synthetic-stream → credentialed vendors (alpaca, polygon) → BYOD local sources.
- Re-export everything at `tradinglab.data.*` so the split from the old `tradinglab.data_sources` module is backward-compatible; no caller code needs to change import paths.
- **BYOD sources register last** so the source-selector combobox shows
  built-in vendors first; BYOD entries appear at the bottom of the
  dropdown, named `<root_name>-<subdir>`. The naming collision
  prevention is structural: built-in keys never contain a hyphen
  (except `synthetic-stream` which is hardcoded into the builtins
  set) so the dialog's strip-and-reregister cycle can identify BYOD
  entries by hyphen presence.
- **BYOD opt-out of disk cache**. `register_local_sources` calls
  `disk_cache.mark_no_persist(key)` for every registered local source,
  so reads of imported CSV bars never come from a stale persisted cache entry and
  writes never accumulate. The CSV files on disk are the source of
  truth; the in-memory `_full_cache` (LRU) provides session-level
  responsiveness identically to remote sources.

## Invariants
- `next(iter(user_visible_sources()))` is `"yfinance"` on fresh package load (synthetic sources are filtered out by their `internal=True` flag).
- `next(iter(DATA_SOURCES))` is also `"yfinance"` on fresh package load (registration order: yfinance → synthetic → synthetic-stream → credentialed vendors → BYOD).
- `"synthetic" in DATA_SOURCES` and `"synthetic-stream" in DATA_SOURCES` are True at runtime (smoke tests and sandbox replay dispatch through the registry directly).
- `"synthetic" not in user_visible_sources()` and `"synthetic-stream" not in user_visible_sources()` — they never appear in the toolbar combobox or Settings → Startup parameters source dropdown.
- `fetch_live_data`, `fetch_synthetic_data`, `fetch_synthetic_stream_bootstrap` all match the `DataFetcher` shape.

## Testing
- `check_00_import`, `check_70_fetch_executor`, and any smoke check that fetches data indirectly exercise the registry.

## Known limitations
- Currently registered sources are scoped to USD-denominated US equities and ETFs. Crypto, FX, futures, and international equities are not tested.
- If a source import fails (e.g. yfinance missing), its `register_source` call will not execute but the others still register. The fetcher itself returns `None` on import failure, which the app handles gracefully.
