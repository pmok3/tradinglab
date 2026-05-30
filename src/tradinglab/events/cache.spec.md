# events/cache.py — Spec

## Purpose
Disk-backed cache for fetched `EventBundle`s, mirroring `tradinglab.disk_cache` in API and contract. Keyed by `(source, ticker)` (events are timeframe-agnostic). Lives under `$TRADINGLAB_CACHE_DIR/events/`.

## File format — JSON, NOT pickle (security audit C1)
- One JSON object per file: `<source>__<ticker>.json`. The object
  carries `"schema": 1` (for forward compat), `symbol`, `fetched_at`,
  and `earnings` / `dividends` arrays. NaN floats (e.g. unrevised
  EPS estimate) round-trip as `null` ↔ `math.nan`.
- Prior versions used `pickle`. `pickle.load` is arbitrary-code-
  execution by design — any `.pkl` an attacker could plant in the
  events directory would execute on the next chart open at which
  point DPAPI-decrypted broker credentials are already in
  `os.environ`. The JSON format parses with no code execution.
- Legacy `.pkl` files are NEVER loaded. The one-shot purge in
  `tradinglab.paths` unlinks them on first launch after upgrade.

## Public API
- `load(source, ticker) -> Optional[EventBundle]` — cached bundle or
  `None` if missing/corrupt. Requires at least one canonical bundle
  key in the JSON object (`schema`, `symbol`, `earnings`,
  `dividends`, `fetched_at`); unrelated JSON objects return `None`
  rather than silently rehydrating to an empty bundle.
- `save(source, ticker, bundle) -> None` — atomically persist
  (`tempfile.mkstemp` + `os.replace`); failures swallowed.
- `merge_bundle(old, new) -> EventBundle` — newer entry wins on
  overlap; sorted ascending. Unchanged from the pickle era.

## Dependencies
Internal: `.base`, `..core.io_helpers.read_json`, `..paths.events_dir`. External: `json`, `math`, `os`, `tempfile`,
`time`, `pathlib`.

## Design Decisions
- **JSON, not pickle** (security audit C1) — see the file-format
  section above.
- **Per-bundle, not per-record.** Re-fetches almost always return the
  complete history; per-record granularity adds merge complexity for
  no benefit.
- **Schema version field.** `"schema": 1` lets us bump the layout
  without misinterpreting older blobs (an absent or unknown schema
  number is treated as "drop and re-fetch" by future code).
- **Past-records-are-stable.** Callers can assume past earnings +
  past ex-dividends are correct; mutable zone is the forward window
  (estimates may be revised). `events_fetch_ttl_seconds` lets the
  caller re-fetch the forward zone without losing the past.
- **`merge_bundle` lets the new side win on overlap.** Same as
  `disk_cache.merge_candles`. Provider revisions of historical rows
  are rare but must be picked up.
- **No TTL enforced here.** Freshness is the caller's job — same
  posture as the candle cache.

## Invariants
- `save` is atomic: a crash mid-write cannot leave a half-written
  JSON object.
- `load` returns `None`, never raises, on corrupt input.
- `load` returns `None` on a JSON object that doesn't have ANY
  canonical bundle key (defends against silent empty-bundle
  rehydration).
- `merge_bundle(None, None)` returns an empty bundle, not `None`.

## Algorithm
1. `load(source, ticker)` reads the per-(source, ticker) `.json`.
2. On miss/corrupt, caller invokes provider fetcher.
3. Caller calls `merge_bundle(cached, fresh)` to combine.
4. Caller calls `save(source, ticker, merged)`.

## Testing
- `tests/unit/events/test_cache.py` — round-trip, atomicity,
  corrupt-blob → `None`, wrong-shape → `None`.

## Known limitations
- No size cap. Bundles are ~KB so unbounded is fine for v1 universes.
- No checksum on save. JSON corruption detected at load via
  try/except; a CRC would distinguish "corrupt" from "pre-feature
  shape".
