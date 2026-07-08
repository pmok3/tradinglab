# preload/manifest.py — Spec

## Purpose
Durable JSON sidecar describing a prepared sandbox universe (which symbols, which intervals, when), plus coverage queries against the disk cache. Single source of truth a sandbox session reads at session-start to know which tickers are inside the universe (strict-offline gating).

## Public API
- `@dataclass(frozen=True) class SymbolEntry` — `symbol`, `intervals` (Tuple[str,...]), `last_fetched` (float epoch). `to_dict` / `from_dict`.
- `@dataclass(frozen=True) class UniverseManifest` — `id`, `name`, `kind` (`"basket"` | `"watchlist"`), `source`, `intervals`, `symbols: Tuple[SymbolEntry,...]`, `prepared_at`. `symbol_set() -> frozenset[str]` for O(1) membership tests. `to_dict` / `from_dict`.
- `@dataclass(frozen=True) class CoverageReport` — `target_date`, `interval`, `covered: Tuple[str,...]`, `missing: Tuple[str,...]`. `covered_count` / `total_count` properties.
- `coverage_for_date(manifest, target_date, interval) -> CoverageReport` — for each manifest symbol, reads `disk_cache.load(manifest.source, sym, interval)` and tests whether any bar lands on `target_date`. Read-only; never fetches. **NOT safe to call on the Tk thread for manifests with > 500 symbols** — full-exchange manifests (NYSE / NASDAQ) trigger O(N) JSONL cache reads that take 5–15 s warm / 30–60 s cold on Windows; callers MUST dispatch off-thread (worker `threading.Thread` + `after()` poller) for those.
- `save(manifest) -> Path` — atomic JSON write via the shared
  `tradinglab.core.io_helpers.atomic_write_json` helper
  (`temp + os.replace` + `fsync`).
- `load(uid) -> Optional[UniverseManifest]` — lenient single-manifest read.
- `load_all() -> List[UniverseManifest]` — enumerate every sidecar, sorted by `prepared_at` descending. Corrupt files skipped silently.
- `delete(uid) -> bool` — drop the sidecar (does NOT touch disk_cache JSONL files).
- `build_from_loaded(*, uid, name, kind, source, intervals, per_symbol, previous=None) -> UniverseManifest` — construct from preload-service output. Symbols with empty interval tuples are dropped (strict-offline must not admit them). When `previous` is provided (the manifest currently on disk for the same UID), per-symbol interval sets are **unioned** with the prior run's, and prior-only symbols are carried forward unchanged. This protects against the "re-run with smaller interval set silently drops bars" failure mode: the underlying JSONL cache entries still exist on disk (the disk-cache short-circuit means the new run never touched them, so they remain valid), and the manifest is the only thing telling strict-offline gating about them. Manifest-level `intervals` is the union of `previous.intervals` and the run's `intervals` so gating sees the full set. To intentionally shrink a manifest, call `delete(uid)` first.

## Dependencies
- Internal: `..disk_cache` (for `_cache_dir()` and `load()`).
- External: `json`, `os`, `time`, `pathlib`, `dataclasses`.

## Filesystem layout
- Manifests live at `<_cache_dir()>/universes/<safe_filename(id)>.json`.
- Co-located with `disk_cache` JSONL files so a single `TRADINGLAB_CACHE_DIR` redirect captures both (smoke-test isolation).
- IDs are stable strings: built-in baskets use the basket key (`sp500`, `qqq`); custom watchlists use `watchlist:<name>`; path-unsafe chars in the filename stem (`<>:"/\|?*`) are replaced with `__`. Whitespace is preserved.
- **`_safe_filename` is non-injective**: every reserved char maps to the same `"__"` replacement. Two universe IDs that differ only in reserved chars (e.g. `a:b` and `a/b`) collide to the same sidecar filename. In practice this is fine — built-in IDs are tightly controlled and watchlist names go through a sanitizer at creation time — but downstream code must not assume the manifest filename is a reversible encoding of the ID.

## Design Decisions
- **JSON, not JSONL**, because manifests are tiny single structured records that humans may want to inspect or hand-edit; the candle cache's one-record-per-line layout is unnecessary here.
- **Atomic writes** delegate to `tradinglab.core.io_helpers.atomic_write_json`,
  which mirrors `disk_cache.save()` discipline: `temp + os.replace` + `fsync`.
- **Lenient reads** match the rest of the cache layer: corrupt sidecars are non-fatal; the GUI list still populates from surviving files.
- **Coverage check is read-only** by design. The "ready to use" view is a decision-support tool; preloading more bars is a separate explicit action.
- **`schema_version` written on every save**, ignored on read for now. Reserved for future breaking changes.
- **`build_from_loaded` drops empty-interval symbols** so the strict-offline gate cannot admit a ticker with no usable bars.
- **`build_from_loaded` unions with prior manifest by default.** The dialog call-site loads the existing manifest for the UID and threads it through as `previous`. This makes a re-run with a smaller interval set, or a re-run with the cancel button hit halfway through, *additive* against the prior coverage rather than destructive. The disk-cache short-circuit is what guarantees correctness — already-fetched bars are still on disk, and the manifest is just the index over them. Pass `previous=None` to opt out (e.g. tools that intentionally rebuild from scratch).
- **`kind` is metadata, not authority**: gating logic only looks at `symbol_set()`.

## Invariants
- `UniverseManifest.to_dict() → from_dict → to_dict` round-trips byte-stable.
- `manifest.symbol_set()` matches `[e.symbol for e in manifest.symbols]` (no duplicates — `build_from_loaded` deduplicates via `dict[str, ...]` input).
- `load_all()` returns a list sorted by `prepared_at` descending.
- Corrupt sidecars are silently skipped, never raised.
- `delete(uid)` does not touch disk_cache JSONL files.
