# data/local_source.py — Spec

## Purpose
BYOD (Bring Your Own Data) fetcher. Reads CSV files the user has placed
on disk (one root → many `<SOURCE>/<TICKER>_<INTERVAL>.csv` files) and
turns them into `List[Candle]` the rest of the app consumes identically
to any other source. Strict, schema-validated, lossless round-trip with
the matching exporter (`data/local_export.py`).

## Public API
- `make_local_fetcher(root: Path) -> DataFetcher` — factory that closes
  over a single subfolder path and returns a `(ticker, interval) ->
  Optional[List[Candle]]` callable. Errors return `None` and are logged
  via the module logger; the function NEVER raises (preserves the
  `data/base.py` contract).
- `discover_subsources(root_path, root_name) -> list[(combobox_key,
  subdir_path, fetcher)]` — walks `root_path`, returns one tuple per
  top-level subdirectory with the combobox key `f"{root_name}-{subdir}"`.
  Hidden directories (starting with `.`) are skipped silently. Sorted
  alphabetically.
- `list_symbols(root: Path) -> list[(ticker, interval)]` — preview
  helper used by the Configure Local Data dialog; ignores non-`.csv`
  files silently.
- `CANONICAL_HEADER` — the six-tuple `("timestamp", "open", "high",
  "low", "close", "volume")` enforced verbatim.
- `LocalDataError` — internal exception raised by the strict parser;
  never propagated to the caller.
- `DOCS_HINT` — public sentinel string referenced from error messages
  pointing the user at `docs/LOCAL_DATA.md`.

## Dependencies
- Internal: `..constants.classify_session`, `..constants.is_intraday`,
  `..models.Candle`, `.base.DataFetcher`.
- External: `csv` (stdlib), `datetime.datetime.fromisoformat` (stdlib).
- No pandas / no numpy. Hot path is pure Python `csv.reader`.

## Design Decisions
- **Strict canonical schema, no aliases**. Header must be
  `timestamp,open,high,low,close,volume` lowercase, in this exact order.
  No "open_price" / "Time" / "Adj Close" auto-mapping. Reasoning: the
  export companion writes this schema verbatim, so round-trip is
  guaranteed; users importing third-party CSVs must convert once,
  upfront, rather than diagnose silent column-mapping bugs forever.
  Error message always includes `DOCS_HINT`.
- **ISO-8601 with explicit timezone offset REQUIRED**. Naive timestamps
  rejected. The `Z` shorthand is accepted (normalised to `+00:00` for
  Python <3.11 compatibility). Reasoning: tz-drift is the #1 silent-bug
  source in user-supplied time series.
- **OHLC: finite + non-negative**. NaN / inf / negative values are
  rejected with a row-numbered error. Zero is allowed (penny-stock
  prints can hit exactly zero on some venues).
- **Volume: blank → 0, float-strings coerced to int**. Broker exports
  vary (`"1234"`, `"1234.0"`, `"1.234e3"`). Negatives are rejected.
- **Duplicates: keep first, log warning**. Same-timestamp rows are
  deduplicated at parse time. A `LOG.warning` records every drop so
  the user can audit.
- **Sort by timestamp ascending**. Sources that write unsorted (e.g.
  reverse-chronological exports from some brokers) are tolerated; the
  parser sorts before yielding.
- **`utf-8-sig` encoding + `newline=""`**. Tolerates UTF-8 BOM (common
  silent header-mismatch source) and both CRLF / LF line endings without
  a separate `.replace("\r", "")` pass.
- **Subfolder discovery skips hidden dirs**. `.DS_Store`, `.git`, etc.
  are not treated as sources.
- **Fetcher closure, not class**. Matches the rest of the source
  modules (`fetch_live_data`, `fetch_synthetic_data`, …) — kept
  identical so `DATA_SOURCES.values()` is uniform.

## Invariants
- `make_local_fetcher(root)(ticker, interval)` returns `None | List[Candle]`.
  Never raises.
- Returned `Candle.date` always has `tzinfo` (the parser would have
  rejected anything else).
- Returned list is sorted by `date` ascending, no duplicate timestamps.
- For intraday intervals, `Candle.session` is `pre|regular|post|gap` per
  `classify_session`. For non-intraday, always `"regular"`.

## Testing
`tests/unit/data/test_local_source.py` — 38 tests covering header
validation, timestamp parsing (tz-required), float parsing (NaN / inf /
negative rejection), volume coercion, duplicate dedupe, sort order,
UTF-8 BOM + CRLF tolerance, fetcher contract (never raises),
subsource discovery (hidden dir skip, alphabetical sort, multi-subdir
fan-out), `list_symbols` preview.

## Known limitations
- **No file-mtime watching**. Within a session, the in-memory
  `_full_cache` (LRU) is authoritative. Users must restart the app to
  pick up edits to CSV files on disk. This is a deliberate choice — the
  alternative (mtime polling) would introduce a class of "did my chart
  refresh?" bugs that doesn't exist in the network sources.
- **No incremental load**. The whole file is read on every fetch (post-
  cache miss). Files with millions of bars will take seconds. Mitigation:
  split very long histories across multiple intervals or use a vendor
  source for high-frequency data.
- **`<TICKER>_<INTERVAL>.csv` filename pattern is rigid**. Tickers with
  spaces or special characters are not supported (the parser uppercases
  but otherwise expects A-Z 0-9 ._-).
