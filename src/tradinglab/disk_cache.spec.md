# disk_cache.py — Spec

## Purpose
Durable cache of fetched candle data, keyed by `(source, ticker, interval)`. Acts as a log of every bar we've ever seen for a key, so that historical bars which fall outside a provider's current window (e.g. yfinance's 60-day intraday cap) are retained across sessions. Freshness policy is **not** enforced here — sealed OHLCV bars are immutable facts; the caller (`ChartApp._cache_is_stale`) decides when to re-fetch.

## File format — JSON Lines, NOT pickle (security audit C1)
- On-disk file is `<source>__<ticker>__<interval>.jsonl` — one
  candle per line as a JSON object (`{"d": "ISO-8601", "o": …, "h":
  …, "l": …, "c": …, "v": int, "s": "regular"}`). NaN price floats
  (gap candles) are emitted as `null` and rehydrate to `math.nan`
  so the format is strict-JSON valid.
- Prior versions used `pickle`. `pickle.load` is arbitrary-code-execution
  by design — any `.pkl` an attacker could plant in the cache directory
  (same-user malware, a "look at this chart" support hand-off, a
  tampered backup) would execute on the next chart open. The JSON
  format parses with no code execution and degrades cleanly on
  corruption (`json.JSONDecodeError` → cache miss).
- **Legacy `.pkl` files are NEVER loaded.** A one-shot purge in
  `tradinglab.paths._purge_legacy_pickle_caches` unlinks any leftover
  `.pkl` files in the cache root and `events/` subdir on first
  launch after the upgrade. The user pays one re-fetch per chart.

## Public API
- `_cache_dir() -> Path` — returns the directory rooted via
  `tradinglab.paths.cache_dir()`. Honors `TRADINGLAB_CACHE_DIR`
  (legacy) and `TRADINGLAB_DATA_DIR` (new) — see `paths.spec.md`.
- `_path_for(source, ticker, interval) -> Path` — filename is
  `f"{source}__{safe_ticker}__{interval}.jsonl"` with `/` and `\`
  in ticker replaced by `_`.
- `load(source, ticker, interval) -> Optional[List[Candle]]` — streams
  the JSONL file line by line; one corrupt line is skipped, an
  all-corrupt file returns `None`. Legacy `.pkl` files are
  intentionally NEVER opened.
- `save(source, ticker, interval, candles)` — atomic write
  (`tempfile.mkstemp` in the same directory + `os.replace`). Writes
  one JSON object per line via `_candle_to_dict`.
- `list_entries() -> List[Tuple[source, ticker, interval]]` — walks
  the cache dir and reverse-parses every
  `<source>__<ticker>__<interval>.jsonl` filename. Returns a sorted
  list. Used by the Export Bars to CSV dialog to enumerate what's
  available for export. Files that don't match the canonical filename
  pattern are silently ignored.
- `merge_candles(old, new) -> List[Candle]` — merges by `date`,
  newer wins on overlap.
- `mark_no_persist(source) / unmark_no_persist(source) /
  is_no_persist(source) / clear_no_persist()` — opt-source-out-of-
  persistence registry. When a source name is in the no-persist set,
  `load()` returns `None` immediately (without touching disk) and
  `save()` is a no-op. Used by BYOD.

## Dependencies
- Internal: `.models.Candle`, `tradinglab.paths.cache_dir`.
- External: `json`, `math`, `os`, `tempfile`, `datetime`, `pathlib`.

## Design Decisions
- **JSON Lines, not pickle** (security audit C1) — see the file-format
  section above. The serialization detour is the price of closing the
  same-user RCE surface.
- **Atomic writes** (`mkstemp` + `os.replace`) guard against a
  mid-write crash leaving a truncated file that fails to parse on
  next boot.
- **Per-line streaming** keeps memory bounded on very large caches
  (a 5-year-of-1m AAPL cache is ~750k lines, ~50 MB). The legacy
  pickle path required loading the whole list into memory before
  yielding.
- **NaN → null → nan round-trip.** Gap candles preserve their NaN
  prices through the JSONL format via `_candle_to_dict` (emits
  `null`) and `_candle_from_dict` (rehydrates `null` to `math.nan`).
  Strict-JSON compatible — no `Infinity`/`NaN` literals.
- **`fromisoformat()` for dates.** Round-trips tz-aware and tz-naive
  datetimes losslessly. Cross-process date precision matches what
  the fetchers emit.
- **Corrupt-line tolerance.** A single bad JSON line is skipped
  (not fatal). If every line is bad, `load()` returns `None` and
  the caller re-fetches.
- **Per-source persistence opt-out** unchanged from the pickle era;
  BYOD sources stay opted out.

## Invariants
- After `merge_candles(old, new)` on the happy path, the result is
  sorted by `date` ascending.
- On overlap by `date`, the entry from `new` wins.
- `load()` never raises — corrupt → `None`.
- `save()` either replaces the destination atomically or leaves the
  prior file intact.
- **Single-instance assumption — last-writer-wins** — no advisory
  locking. Two processes writing simultaneously will silently
  overwrite. Run only one TradingLab instance per cache directory.
- `.pkl` files in the cache directory are NEVER opened by this
  module. The legacy-purge in `paths._migrate_legacy_locations`
  unlinks them on first launch after upgrade.

## Testing
- `tests/unit/test_disk_cache_merge.py` — save/load round-trip and
  merge-on-fetch.
- `tests/unit/test_disk_cache_list_entries.py` — `list_entries()`
  filename enumeration.
- `tests/unit/test_paths_purge_pkl.py` — one-shot legacy-`.pkl`
  purge.
- `check_e0_disk_cache_persist` — end-to-end smoke covering the
  save→load round trip.

## Known limitations / Future work
- No eviction (disk-space-bounded cleanup). Directory grows
  unbounded over time; users with many tickers × intervals could
  accumulate hundreds of MB.
- JSONL is ~3-4x larger than the equivalent pickle on disk (text
  encoding overhead). Acceptable trade-off for the RCE-surface
  removal; Parquet (smaller and faster) was rejected because
  pyarrow has had its own CVE history.