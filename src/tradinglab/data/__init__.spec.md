# data/__init__.py — Spec

## Purpose
Aggregates the data-source plugins (yfinance, synthetic, synthetic-stream bootstrap) into a single importable registry. Also re-exports the normalization helpers (`candles_from_dataframe`, `CandleArrays`, `stash_arrays`/`pop_prebuilt_arrays`) and the parallel-fetch primitive, all at `tradinglab.data.*` for backward compatibility with the flat pre-split layout (`tradinglab.data_sources`).

## Public API
- `DATA_SOURCES: Dict[str, DataFetcher]` — the registry, re-exported from `.base`. UI uses the first inserted key as default.
- `DataFetcher` — type alias `Callable[[str, str], Optional[List[Candle]]]`.
- `register_source(name, fetcher)` — imperative registration.
- `fetch_live_data` (yfinance), `fetch_synthetic_data`, `fetch_synthetic_stream_bootstrap` — the three deterministically-registered built-in fetchers.
- `fetch_schwab_data`, `fetch_alpaca_data`, `fetch_polygon_data` — vendor adapters. `"alpaca"` and `"polygon"` are registered when their respective credentials are available, else inert. **`"schwab"` is currently NOT registered** even when credentials are configured — `schwab_source._http_get_pricehistory` is still a `NotImplementedError` stub, so the registration line in `data/__init__.py` is commented out to keep a broken option out of the source-selector dropdown. Re-enable once the REST GET lands. Each adapter builds requests against the vendor's REST endpoint and routes the response through `candles_from_json_rows` for normalization.
- `candles_from_schwab_response`, `candles_from_alpaca_response`, `candles_from_polygon_response` — vendor-specific response-shape adapters that produce the `(rows, keymap, ts_unit)` triple consumed by `candles_from_json_rows`. Exported for unit-testing parity with the live adapters.
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
  so reads of imported CSV bars never come from a stale pickle and
  writes never accumulate. The CSV files on disk are the source of
  truth; the in-memory `_full_cache` (LRU) provides session-level
  responsiveness identically to remote sources.

## Invariants
- `next(iter(DATA_SOURCES))` is `"yfinance"` on fresh package load.
- `fetch_live_data`, `fetch_synthetic_data`, `fetch_synthetic_stream_bootstrap` all match the `DataFetcher` shape.

## Testing
- `check_00_import`, `check_70_fetch_executor`, and any smoke check that fetches data indirectly exercise the registry.

## Known limitations
- Currently registered sources are scoped to USD-denominated US equities and ETFs. Crypto, FX, futures, and international equities are not tested.
- If a source import fails (e.g. yfinance missing), its `register_source` call will not execute but the others still register. The fetcher itself returns `None` on import failure, which the app handles gracefully.

