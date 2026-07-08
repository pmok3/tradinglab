# data/local_export.py — Spec

## Purpose
Symmetric companion to `data/local_source.py`. Writes selected
`(source, ticker, interval, candles)` tuples from the disk cache to CSV
files on disk in the strict canonical schema, so the destination folder
can be dropped into Configure Local Data → become a new root → load
back identical bars.

## Public API
- `format_csv(candles: Sequence[Candle]) -> str` — pure in-memory
  CSV formatter (no disk I/O). Used by the zip exporter and by
  tests that want to assert on content without touching the
  filesystem. Raises `LocalExportError` for naive timestamps.
- `write_csv(path: Path, candles: Sequence[Candle]) -> int` — atomic
  write of one file (`.tmp` + `os.replace`). Calls `format_csv`
  under the hood. Creates parent dirs as needed. Returns rows
  written. Raises `LocalExportError` if any candle has a naive
  timestamp. Retained for callers that want individual CSVs on
  disk; the export dialog itself no longer uses this path.
- `export_entries(entries, destination) -> list[(source, ticker,
  interval, rows_written, error)]` — batch export driver to a
  destination folder. For each input `(source, ticker, interval,
  candles)`, writes `<destination>/<SOURCE>/<TICKER>_<INTERVAL>.csv`
  and returns a per-entry result tuple. NEVER raises for a single
  bad entry. Raises `LocalExportError` only if the destination
  parent directory does not exist. Legacy folder-of-CSVs API;
  retained for programmatic callers. The Export Bars to CSV
  dialog now writes a zip exclusively.
- `export_entries_zip(entries, zip_path) -> list[(source, ticker,
  interval, rows_written, error)]` — zip variant. Streams each
  entry's CSV into a single `ZIP_DEFLATED` archive at `zip_path`
  with arcname `<SOURCE>/<TICKER>_<INTERVAL>.csv` (forward slash
  separator per the PKZIP spec). Atomic publish via `.tmp` +
  `os.replace`. Audit `local-export-zip`.
- `default_zip_filename(today=None) -> str` — returns
  `tradinglab-export-YYYY-MM-DD.zip` using local date (or the
  injected `today` for tests). Used as the prepopulated default
  in the Export Bars to CSV dialog.
- `LocalExportError` — raised by `format_csv` / `write_csv` /
  `export_entries_zip` for invalid input.
- `ExportEntry` — frozen dataclass with `(source, ticker, interval,
  bar_count, first_ts, last_ts)`. Currently unused by the dialog
  (which renders metadata inline); reserved for future preview UI.

## Dependencies
- Internal: `..models.Candle`.
- External: `csv`, `io`, `zipfile` (stdlib), `os.replace`
  (stdlib atomic rename), `datetime.date` (default-filename
  date stamp).

## Design Decisions
- **Zip exclusively in the dialog** (audit `local-export-zip`).
  The Export Bars to CSV dialog now produces a single
  deflate-compressed `.zip` rather than a folder of loose CSVs.
  Saves transfer space (~4x on OHLCV text) and gives the user
  one file to share / back up. The folder-mode `export_entries`
  function is retained as a programmatic API for callers that
  prefer per-file output.
- **Default filename `tradinglab-export-YYYY-MM-DD.zip`**.
  Local date, deterministic so the user knows what they're
  producing. The dialog still shows the picker, so the user
  can override; a missing `.zip` suffix is forced by the
  dialog's `_on_browse` so the suffix matches the chosen
  format filter.
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
  a future option; deferred because most cache entries are <1MB on disk.
