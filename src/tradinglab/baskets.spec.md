# baskets.py — Spec

## Purpose
Resolve the well-known basket names ("S&P 500", "Nasdaq-100 / QQQ", "NYSE", "NASDAQ") into concrete ticker-symbol lists for the sandbox universe-preload feature. Pure data; no network, no I/O beyond reading CSVs that already ship in the repo. The GUI consumes this module to populate the Universe radios in the prepare-universe dialog without reaching into `tools/`.

## Public API
- `sp500_symbols() -> List[str]` — reads `tools/sp500.csv` (resolved relative to this module's repo root, not cwd) and returns the constituent symbols. Mirrors the ticker-munging the existing batch fetcher does (`.` -> `-` so `BRK.B` becomes `BRK-B` for yfinance). Raises `FileNotFoundError` with a clear message if the CSV is missing.
- `qqq_symbols() -> List[str]` — returns a fresh list copy of the hardcoded Nasdaq-100 snapshot. Snapshot date is exposed as `QQQ_LAST_REFRESHED`.
- `nyse_symbols() -> List[str]` — reads `tools/nyse.csv` (NYSE-proper / Big Board common stock, ~2,000 symbols). Refresh via `python tools/refresh_exchange_lists.py`. Pre-munged at snapshot time (`BRK.B` -> `BRK-B`). Raises `FileNotFoundError` if missing.
- `nasdaq_symbols() -> List[str]` — reads `tools/nasdaq.csv` (NASDAQ-listed common stock, ~2,900 symbols). Same refresh path. Does NOT apply dot-munging (NASDAQ's feed doesn't use dots for class shares).
- `QQQ_LAST_REFRESHED: str` / `NYSE_LAST_REFRESHED: str` / `NASDAQ_LAST_REFRESHED: str` — ISO date strings for each snapshot. The dialog surfaces these so traders can tell when each basket was last refreshed. NYSE/NASDAQ constants are updated in place by the refresh CLI via regex.
- `BUILTIN_BASKETS: Dict[str, Callable[[], List[str]]]` — keyed registry: `{"sp500", "qqq", "nyse", "nasdaq"}`. The keys are stable IDs used in the manifest sidecar; renaming a label must not break old manifests.
- `BUILTIN_BASKET_LABELS: Dict[str, str]` — display labels for the GUI. Decoupled from `BUILTIN_BASKETS` keys deliberately.
- `BUILTIN_BASKET_REFRESHED_DATES: Dict[str, str]` — per-basket snapshot dates so dialogs can render a "refreshed YYYY-MM-DD" suffix without reaching for module-level constants. SP500 is intentionally absent (Wikipedia-derived CSV has no baked-in date); dialogs skip the suffix when a key is missing.
- `FULL_EXCHANGE_BASKETS: frozenset` — `{"nyse", "nasdaq"}`. Used by the prepare-universe dialog to gate the amber survivorship banner; future full-exchange baskets get the treatment automatically.

## Dependencies
- Internal: `_resources.resource_path` (frozen-mode-aware `tools/*.csv` path
  resolution).
- External: `csv`, `pathlib`.

## Design Decisions
- **Centralized CSV loader (`_load_symbols_csv`).** The three CSV-backed loaders (sp500, nyse, nasdaq) share a single helper to keep CSV-shape drift between snapshots impossible. The helper takes an optional `munge_dots` flag so NASDAQ's already-clean feed bypasses dot-translation.
- **Hardcoded QQQ snapshot, not scraped.** Wikipedia / Invesco scraping is fragile (table layouts change, CDN throttling, bot-detection), and the failure mode of a stale snapshot is benign: the preload's per-symbol failure list collects any delisted / renamed tickers and the user can decide whether to continue with the partial universe. A `QQQ_LAST_REFRESHED` constant keeps freshness visible.
- **`tools/sp500.csv` is the source of truth for SP500.** It already ships in the repo for the existing `tools/universe_cache.py` batch fetcher; duplicating it under `src/` would create a synchronisation hazard. The loader resolves the path via `_resources.resource_path` (handling both source checkouts and PyInstaller frozen bundles under `_internal/tools/`), so cwd-independent.
- **Every CSV-backed basket MUST be bundled by `TradingLab.spec`.** `sp500.csv`, `nyse.csv`, and `nasdaq.csv` are all resolved via `_resources.resource_path("tools", …)`, which points at `_internal/tools/` in the frozen build. `TradingLab.spec` bundles all three in one loop; if a CSV is not bundled, its loader raises `FileNotFoundError` in the `.exe` and the prepare-universe dialog shows **0 symbols** for that basket (the shipped NYSE/NASDAQ-empty bug — only sp500 was bundled originally). QQQ needs no CSV (hardcoded list). Guarded by `tests/unit/test_baskets_exchange.py::test_tradinglab_spec_bundles_every_csv_backed_basket`.
- **NYSE & NASDAQ snapshots ship as `tools/{nyse,nasdaq}.csv` with a canonical 4-column schema** (`Symbol,Name,Exchange,SnapshotDate`) decoupled from NASDAQ Trader's pipe-delimited vendor format. Curation (drop preferreds, warrants, units, rights, ETFs, halted / deficient / bankrupt names) happens at snapshot-build time inside `tools/refresh_exchange_lists.py`, NOT here — keeping this module's hot path a single CSV read.
- **NYSE-proper only.** `nyse_symbols()` is `Exchange='N'` in NASDAQ Trader's `otherlisted.txt`, i.e. the Big Board. NYSE American (A), Arca (P, mostly ETFs), and Cboe BZX (Z) are excluded. Future composite or per-venue baskets can be separate entries in the registry without renaming this one.
- **No network, no caching.** This is a pure resolver. The preload service does the network work; the disk_cache does the persistence work; this module just answers "what symbols are in basket X?".
- **`.`-to-`-` munging matches the existing tools loader** so callers are interoperable. A change here would silently mismatch the cache keys written by `tools/universe_cache.py`. The NYSE refresh script pre-munges its CSV, so the loader's munge step is redundant-safe.
- **Display labels separate from IDs.** The manifest sidecar persists basket IDs (`"sp500"`, `"qqq"`, `"nyse"`, `"nasdaq"`); changing the human-facing label doesn't invalidate old manifests.
- **`FULL_EXCHANGE_BASKETS` as a frozenset, not a class attribute.** Lookup site (dialog) treats it as data, so a frozenset is the right shape; future fourth/fifth exchanges just add a member here and inherit the banner gating for free.

## Invariants
- `sp500_symbols()`, `qqq_symbols()`, `nyse_symbols()`, `nasdaq_symbols()` return non-empty lists when their backing CSV/list is present.
- Each call returns a fresh `list` — callers may mutate without poisoning the snapshot.
- All symbols in the returned lists are upper-case and yfinance-compatible (no dots).
- `BUILTIN_BASKETS` and `BUILTIN_BASKET_LABELS` keys are in sync — every key in one appears in the other.
- `FULL_EXCHANGE_BASKETS` is a subset of `BUILTIN_BASKETS` keys.

