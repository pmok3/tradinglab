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
  all-corrupt file returns `None`. **Returns `None` immediately for
  ratio pseudo-symbols** (`_is_ratio_ticker` — `AMD/NVDA`, etc.) and
  for `mark_no_persist` sources, before touching the
  filesystem. Legacy `.pkl` files are
  intentionally NEVER opened. Bars whose OHLC is not all-finite are
  dropped on read (`_drop_nonfinite_ohlc`) so a stale poison bar can
  never reach the cache or render. **Heal-on-load persistence:** when
  poison bars are actually dropped, `load` atomically rewrites the
  cleaned file (via `save`) so the NaN-OHLC line is erased from disk —
  not merely filtered on every subsequent read. This stops the poison
  row from lingering forever (dropped on load but never erased), which
  otherwise keeps the series' visible tail under-reporting the last
  real bar and lets the stale row re-surface through any raw-file
  reader. The rewrite is best-effort (`save` swallows write errors) and
  only fires when a drop occurred (`_drop_nonfinite_ohlc` returns the
  same list object when nothing was dropped — an identity check, no
  second scan); a clean file is never rewritten. `load` still never
  raises.
- `save(source, ticker, interval, candles)` — atomic write
  (`tempfile.mkstemp` in the same directory + `os.replace`). Writes
  one JSON object per line via `_candle_to_dict`. **No-op for ratio
  pseudo-symbols** (`_is_ratio_ticker`) and `mark_no_persist` sources.
- `_is_ratio_ticker(ticker) -> bool` — true for a ratio pseudo-symbol
  (`AMD/NVDA` slash form). Lazy-imports
  `data.ratio_source.is_ratio_symbol` to avoid a module-load import
  cycle (`data` imports `disk_cache`); falls back to a `"/" in ticker`
  check if the import fails. **Why ratios are never persisted:** a
  ratio is *derived* from its two legs (which DO cache individually);
  persisting it would force slugging the filename-illegal `/` (lossy →
  `list_entries`/cache-export pollution) and risk the cached ratio
  going stale vs its legs. The in-memory `_full_cache` still gives
  session-level responsiveness. See `data/ratio_source.spec.md`.
- `list_entries() -> List[Tuple[source, ticker, interval]]` — walks
  the cache dir and reverse-parses every
  `<source>__<ticker>__<interval>.jsonl` filename. Returns a sorted
  list. Used by the Export Bars to CSV dialog to enumerate what's
  available for export. Files that don't match the canonical filename
  pattern are silently ignored.
- `merge_candles(old, new, *, presorted=False) -> List[Candle]` — merges
  by `date`, newer wins on overlap. Sorted inputs use a linear two-pointer
  merge; unsorted inputs fall back to the dict+sort path. `presorted=True`
  lets a caller that knows both inputs are already date-ascending skip the
  two O(N) sortedness scans (~5.6ms on an 11k-bar pair) — used on the live
  load/prefetch paths where the disk file is saved sorted and fetchers
  return time-ordered data. The merged result is passed through
  `_drop_nonfinite_ohlc`, so a non-finite-OHLC bar present on either side
  (typically a stale poison bar already on disk) is removed and never
  re-persisted.
- `_is_finite_ohlc(c) / _drop_nonfinite_ohlc(candles)` — row-validity
  gate mirroring `data.normalize`: a bar with NaN/±Inf OHLC carries no
  price and is dropped. `_drop_nonfinite_ohlc` returns the original list
  object unchanged on the all-finite fast path (no spurious copy / object
  identity preserved); it only allocates a filtered copy when a poison
  bar is present.
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
- **NaN → null → nan format round-trip, but non-finite-OHLC bars are
  dropped on load/merge.** The on-disk format itself is lossless for
  NaN prices (`_candle_to_dict` emits `null`, `_candle_from_dict`
  rehydrates `null` to `math.nan`) and stays strict-JSON compatible (no
  `Infinity`/`NaN` literals). However, a bar with non-finite OHLC is not
  a valid price bar — providers (Yahoo especially) occasionally emit a
  corrupt daily row with NaN OHLC + a real volume for a day that traded.
  The fetch normalizers drop such rows from *fresh* data, but once one is
  on disk, fresh data never carries that date again to overwrite it and
  `merge_candles` would retain the non-overlapping stale bar forever —
  it then renders as an invisible NaN candle behind a visible volume bar
  ("today's OHLC is missing but I can still see the volume"). `load()`
  and `merge_candles()` therefore drop non-finite-OHLC bars, which heals
  the cache on the next read/merge. Gap candles (`Candle.gap()`) are a
  compare-view render artifact produced AFTER load/merge by
  `core.pairing.align_pair` and are never persisted, so this filter does
  not affect them.
- **`fromisoformat()` for dates.** Round-trips tz-aware and tz-naive
  datetimes losslessly. Cross-process date precision matches what
  the fetchers emit.
- **Corrupt-line tolerance.** A single bad JSON line is skipped
  (not fatal). If every line is bad, `load()` returns `None` and
  the caller re-fetches.
- **Per-source persistence opt-out** unchanged from the pickle era;
  BYOD sources stay opted out.
- **Linear merge fast path.** Provider and cache outputs are expected to
  be sorted ascending by `date`. `merge_candles` detects sorted inputs
  and performs an O(N+M) two-pointer merge that collapses duplicate
  date runs with "last within side wins, then new side wins". If either
  input is unsorted, it preserves the older dict+sort behavior. Callers
  that already know both sides are sorted pass `presorted=True` to skip
  the two O(N) `_is_sorted_by_date` scans (a mixed-tz pair still raises
  `TypeError` inside `_merge_sorted_candles` and falls back to `list(new)`
  exactly as the auto-detect path does).

## Invariants
- After `merge_candles(old, new)` on the happy path, the result is
  sorted by `date` ascending.
- On overlap by `date`, the entry from `new` wins.
- Duplicate dates within one side collapse to that side's last candle
  for the duplicate date.
- Neither `load()` nor `merge_candles()` ever returns a bar with
  non-finite OHLC — poison bars are dropped (`_drop_nonfinite_ohlc`).
- After `load()` reads a file that contained poison bars, the on-disk
  file no longer contains them (heal-on-load persistence). A clean file
  is left byte-for-byte unchanged (no spurious rewrite).
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
- `tests/unit/test_disk_cache_nonfinite_load.py` — poison-bar drop on
  load, all-poison → `None`, and heal-on-load persistence (the cleaned
  file is rewritten when poison is dropped; a clean file is not).
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