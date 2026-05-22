# watchlists/storage.py — Spec

## Purpose
JSON persistence for watchlists. Single file under the user's cache dir (`_cache_dir() / "watchlists.json"`). Schema-versioned so future breaking changes can be migrated without destroying user data.

## Public API
- `@dataclass class Watchlist`: `name: str`, `tickers: List[str] = []`.
- `_SCHEMA_VERSION = 2`; `_SUPPORTED_VERSIONS = (1, 2)` (v1 is accepted for backward-compat read; writes always use v2).
- `normalize_tickers(tickers) -> List[str]` — upper-case, trim, de-dupe, drop None/empty. Preserves insertion order.
- `_storage_path()`, `load_all() -> Tuple[List[Watchlist], List[str]]`, `save_all(watchlists, pinned)`, `export_to_file(watchlists, path, pinned=None)`, `import_from_file(path) -> Tuple[List[Watchlist], List[str]]`.

## Dependencies
- Internal: `..disk_cache._cache_dir` (so watchlists co-locate with disk cache + settings).
- External: `json`, `pathlib`, `dataclasses`.

## Design Decisions
- **Schema envelope** `{"version": 2, "watchlists": [...], "pinned": [...]}` — v1 files (no `pinned` field) are accepted on read and come back with `pinned=[]`; `WatchlistManager.__init__` then seeds a pin from the first list (migration path, see `manager.spec.md`).
- **Atomic save** (temp file + `Path.replace`) — crash-safe. Writes swallow `OSError` with a `print` rather than raising; losing a save is annoying but not fatal.
- **Corrupt file stays in place** on load failure: `load_all()` returns `([], [])` rather than clobbering user data on a JSON-decode error.
- **`load_all` validates entry shape**: skips non-dict entries and entries missing `name`/`tickers`. Pinned names coerced to a deduped list of strings (non-string entries dropped).
- **`import_from_file` re-normalizes tickers on load** (upper-case + dedupe); `load_all` does NOT normalize (it trusts the file that `save_all` wrote).
- **`export_to_file` / `import_from_file` raise on error** (unlike the internal save/load which swallows): dialogs surface these errors to the user via a messagebox.
- **`pinned` is ordered**: left-to-right order of sub-tabs in the UI maps directly to this list. `reorder_pins` in the manager rewrites this list.
- **Tickers coerced to `str` at the storage boundary** — `normalize_tickers` (used by both `save_all` and `import_from_file`) wraps each entry with `str(t)` before stripping/upper-casing (see `storage.py:97`). Callers that pass non-string tickers (numpy strings, `bytes`, `Path`) get safely-stringified output rather than a crash; the on-disk JSON is always pure-ASCII text.

## Invariants
- `save_all(*load_all()) == load_all()` — idempotent round-trip (modulo version upgrade from v1 to v2).
- `normalize_tickers([" amd ", "AMD", None, ""]) == ["AMD"]`.
- `load_all()` never raises (corrupt file → `([], [])`).
- `import_from_file` raises on version mismatch (unsupported version number).
- `load_all` returns a deduped pinned list (even if the on-disk file has duplicates).

## Data Flow / Algorithm
```
normalize_tickers(tickers):
    out = []
    for t in tickers or ():
        if t is None: continue
        u = str(t).strip().upper()
        if u and u not in out: out.append(u)
    return out
```

## Testing
- `check_d0_dialogs` exercises the watchlist dialog which drives save/load via the manager.
- `check_d13_watchlist_pinned_subtabs` verifies pin-list persistence through the manager.

## Known limitations / Future work
- No migration path from a hypothetical v3; `load_all()` would need an explicit version-switch.

