# watchlists/storage.py — Spec

## Purpose
JSON persistence for watchlists. Single file under the user's cache dir (`_cache_dir() / "watchlists.json"`). Schema-versioned so future breaking changes can be migrated without destroying user data.

## Public API
- `@dataclass class Watchlist`: `name: str`, `tickers: List[str] = []`.
- `_SCHEMA_VERSION = 3`; `_SUPPORTED_VERSIONS = (1, 2, 3)` (v1/v2 accepted for backward-compat read; writes use v3, adding an optional `display` block for configurable columns).
- `normalize_tickers(tickers) -> List[str]` — upper-case, trim, de-dupe, drop None/empty. Preserves insertion order.
- `_storage_path()`, `load_all() -> Tuple[List[Watchlist], List[str]]`, `save_all(watchlists, pinned, display=None)`, `export_to_file(watchlists, path, pinned=None, display=None)`, `import_from_file(path) -> Tuple[List[Watchlist], List[str]]`.
- `load_display() -> dict` / `read_display(path) -> dict` — the v3 `display` block `{default_columns, by_watchlist}` (opaque column-dict JSON; empty for v1/v2 files). `save_all(display=None)` **preserves** the existing block so a plain lists/pins save never wipes columns.

## Dependencies
- Internal: `..disk_cache._cache_dir` (so watchlists co-locate with disk cache + settings).
- External: `json`, `pathlib`, `dataclasses`.

## Design Decisions
- **Schema envelope** `{"version": 3, "watchlists": [...], "pinned": [...], "display": {...}}` — v1 files (no `pinned` field) and v2 files (no `display` block) are accepted on read. Writes use v3; `display` is omitted when empty.
- **Atomic save** (temp file + `Path.replace`) — crash-safe. Writes swallow `OSError` with a `print` rather than raising; losing a save is annoying but not fatal.
- **Corrupt file stays in place** on load failure: `load_all()` returns `([], [])` rather than clobbering user data on a JSON-decode error.
- **`load_all` validates entry shape**: skips non-dict entries and entries missing `name`/`tickers`. Pinned names coerced to a deduped list of strings (non-string entries dropped).
- **`import_from_file` re-normalizes tickers on load** (upper-case + dedupe); `load_all` does NOT normalize (it trusts the file that `save_all` wrote).
- **`export_to_file` / `import_from_file` raise on error** (unlike the internal save/load which swallows): dialogs surface these errors to the user via a messagebox.
- **`pinned` is ordered**: left-to-right order of sub-tabs in the UI maps directly to this list. `reorder_pins` in the manager rewrites this list.
- **Tickers are coerced where untrusted input enters** — `normalize_tickers` wraps each entry with `str(t)` before stripping/upper-casing; `import_from_file` uses it, and `load_all` defensively stringifies stored tickers. `save_all` trusts its `Watchlist` inputs (manager-created lists are already normalized) and writes them as-is.

## Invariants
- `save_all(*load_all()) == load_all()` — idempotent round-trip (modulo version upgrade from v1/v2 to v3).
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
- No migration path from a hypothetical v4; `load_all()` would need an explicit version-switch.
- **`JsonObjectStore[T]` migration deferred.** The watchlists storage uses a single consolidated JSON file (schema v3: `{version, watchlists, pinned, display}`) which doesn't fit the `core.json_collection_store.JsonObjectStore[T]` generic (which assumes one-record-per-file plus an `_index.json`). The watchlists shape also carries sibling `pinned` and `display` metadata that have no per-record `id` to key on. Migration deferred until either (a) we split into per-watchlist files (would require moving `pinned` / `display` to sidecars and reworking `WatchlistManager` re-ordering semantics) or (b) we extend the generic to support a consolidated-file mode.
