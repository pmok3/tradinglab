# watchlists/manager.py — Spec

## Purpose
In-memory watchlist manager with **explicit save** semantics. Mirrors the configuration-file model: nothing auto-loaded on construction, nothing auto-written on mutation. Users explicitly load/save via `File → Load/Save Watchlists…` menu items (or Watchlists dialog `Import…` / `Export…` buttons, which route through the same methods).

## Public API
- `WatchlistManager()` — start empty (no disk I/O on construction).
- Reads: `list_names() -> List[str]`, `all() -> List[Watchlist]`, `get(name) -> Optional[Watchlist]`, `pinned_names() -> List[str]`, `is_dirty() -> bool`, `loaded_path() -> Optional[Path]`.
- Mutators (mark dirty; never touch disk):
  - `create(name, tickers=None) -> Watchlist`
  - `delete(name) -> bool`
  - `rename(old, new)`
  - `add_ticker(name, ticker)` / `remove_ticker(name, ticker)`
  - `set_tickers(name, tickers)`
  - `pin(name)` / `unpin(name)` / `reorder_pins(names)`
  - `import_watchlists(incoming, *, mode="merge"|"replace", pinned: Optional[List[str]] = None) -> int` — bulk merge or replace. When `pinned` is provided (imported file's pin list), each surviving name is appended to the manager's current pin list (de-duped, capped at `MAX_PINNED`, existing pins kept ahead). When `pinned` is None or `[]`, legacy fallback: if watchlists present but no pins, first watchlist's name auto-seeded so UI is never blank. `mode="replace"` with imported pins replaces both lists AND pins; `mode="merge"` extends pin list without dropping existing.
- Explicit file I/O (touch disk; reset dirty + update `loaded_path`):
  - `load_from_file(path) -> int` — replace state with file contents (pins included, clamped to `MAX_PINNED`). Raises on unreadable/malformed.
  - `save_to_file(path)` — write current state. Creates parent dirs. Raises on I/O error.
- `clear()` — wipe everything.

## Dependencies
Internal: `.storage.Watchlist`, `.storage.export_to_file`, `.storage.import_from_file`, `.storage.normalize_tickers`. **No** dependency on `load_all`/`save_all` (still exist in `storage.py` for back-compat but unused by the manager).

## Design Decisions
- **Not observable** — no `subscribe`/`_notify` channel; callers poll on demand or rebuild on explicit action (e.g. `_rebuild_watchlist_subtabs`). Single-process / single-thread / single-window assumption.
- **No auto-load on construction.** Existing `%LOCALAPPDATA%\tradinglab\watchlists.json` is left in place but ignored — users must `File → Load Watchlists…`. Makes session boundaries crisp.
- **No auto-save on mutation.** `_dirty` tracks unsaved changes; `ChartApp._refresh_title` shows trailing `*` in window title.
- **`load_from_file` is *replace*** semantics, not merge. Merge available via `import_watchlists(mode="merge")` (Watchlists dialog `Import…`). Two semantics live side-by-side: merge for "add a friend's curated list", replace for "switch to a different saved profile".
- **Pin sanitization on load**: filtered to existing names, deduped, clamped to `MAX_PINNED`. If file has watchlists but no surviving pin, first name auto-seeded.
- **Errors propagate** from explicit-file methods. Caller (menu/dialog) shows a `messagebox`.

## Invariants
- After `__init__()`: `list_names() == []`, `pinned_names() == []`, `is_dirty() is False`, `loaded_path() is None`.
- After any successful mutator: `is_dirty() is True`. No file in the cache dir touched.
- After `save_to_file(p)`: file is valid v2 JSON, `is_dirty() is False`, `loaded_path() == p`.
- After `load_from_file(p)`: state matches file, `is_dirty() is False`, `loaded_path() == p`. `pinned_names() ⊆ list_names()`. `len(pinned_names()) <= MAX_PINNED`.
- After `clear()`: same as fresh construction.

## MAX_PINNED
Per-instance attribute seeded from the `watchlist_max_pinned` Tunable (default 5, max 20). Class-level `MAX_PINNED = 5` remains as fallback for `WatchlistManager.MAX_PINNED` reads without an instance. Users lift the cap via Settings → "Watchlist sub-tab cap"; new value applies to managers constructed after next launch.
