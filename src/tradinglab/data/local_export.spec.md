# data/local_export.py — Spec

## Purpose
Symmetric companion to `data/local_source.py`. Writes selected
`(source, ticker, interval, candles)` tuples from the disk cache to CSV
files on disk in the strict canonical schema, so the destination folder
can be dropped into Configure Local Data → become a new root → load
back identical bars.

## Public API
- `write_csv(path: Path, candles: Sequence[Candle]) -> int` — atomic
  write of one file (`.tmp` + `os.replace`). Creates parent dirs as
  needed. Returns rows written. Raises `LocalExportError` if any candle
  has a naive timestamp.
- `export_entries(entries, destination) -> list[(source, ticker,
  interval, rows_written, error)]` — batch export driver. For each
  input `(source, ticker, interval, candles)`, writes
  `<destination>/<SOURCE>/<TICKER>_<INTERVAL>.csv` and returns a per-
  entry result tuple. NEVER raises for a single bad entry; the dialog
  reports per-row outcomes. Raises `LocalExportError` only if the
  destination parent directory does not exist.
- `LocalExportError` — raised by `write_csv` for invalid input.
- `ExportEntry` — frozen dataclass with `(source, ticker, interval,
  bar_count, first_ts, last_ts)`. Currently unused by the dialog
  (which renders metadata inline); reserved for future preview UI.

## Dependencies
- Internal: `..models.Candle`.
- External: `csv` (stdlib), `os.replace` (stdlib atomic rename).

## Design Decisions
- **Atomic write per file**. Write to `path + ".tmp"` then
  `os.replace`. A crash mid-export can't leave a half-written file
  the importer would reject. Failed writes clean up the `.tmp` to
  avoid accumulating orphans across repeated failures.
- **Naive timestamps rejected at export time**. If a cached
  `Candle.date.tzinfo is None`, `write_csv` raises
  `LocalExportError`. This catches the bug at the user-visible export
  step rather than at re-import time on someone else's machine where
  the cache may not even exist.
- **`isoformat()` for timestamps**. Preserves whatever tz offset the
  upstream source stamped — typically `-04:00` / `-05:00` for US
  equities, `Z` / `+00:00` for crypto-style sources.
- **`.rstrip('0').rstrip('.')` for OHLC**. Keeps the file readable
  (`100` instead of `100.000000`) without losing precision; the
  importer's `float()` parses both forms identically.
- **Volume → `int`**. The schema demands integer volume; the exporter
  coerces via `int(candle.volume)` (the parser accepts float strings
  for back-compat, but always emits int).
- **`<destination>/<SOURCE>/<TICKER>_<INTERVAL>.csv` layout**. Subfolder
  per source so multiple sources don't collide on a same-named ticker.
  This matches `discover_subsources` in `local_source.py`: drop the
  destination folder into Configure Local Data, and each subfolder
  becomes one combobox entry.
- **Path sanitisation via `_sanitize_segment`**. Strips `/`, `\`, and
  `..` from source / ticker tokens before joining with the destination
  path. Belt-and-suspenders against malformed cache keys; the disk
  cache itself only sanitises tickers, not source names.
- **Refuses to mkdir more than one level deep**. If `destination.parent`
  doesn't exist, raises rather than create a deep tree. Matches typical
  "save as" semantics — user picked a real folder; we create only the
  per-source subfolder beneath it.

## Invariants
- After `write_csv(path, candles)` returns successfully, `path.exists()`
  is `True` and the file is a valid input for `local_source.fetch`.
- `export_entries(...)` returns one result tuple per input entry, in
  input order.
- The exporter and importer share no code besides `..models.Candle` and
  the canonical schema constant — a round-trip test pins integrity.

## Testing
`tests/unit/data/test_local_export.py` — 18 tests covering header /
canonical schema, atomic temp cleanup (success and failure paths),
naive-timestamp rejection, ISO format preserves offset, multi-entry
batch with subfolder layout, ticker uppercased on disk, continues after
single failure, path-segment sanitisation.

The headline test is `TestRoundTrip::test_export_then_import_preserves_candles`
which exports 10 candles, re-imports via `make_local_fetcher`, and
asserts every OHLCV field plus timestamp is bit-identical.

## Known limitations
- **One file per (source, ticker, interval)**. No chunked or paged
  export — for a single very large cache entry the whole thing is
  loaded into memory and written. Acceptable for the target use case
  (sharing a few hundred symbol-intervals at a time).
- **No compression**. Output is plain CSV. Gzip / Parquet support is
  a future option; deferred because the disk cache itself is already
  compressed (pickle) and most cache entries are <1MB on disk.
