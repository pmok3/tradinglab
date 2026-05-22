# recent_files.py — Spec

## Purpose

LRU "most recently used" list of configuration / watchlist file paths,
persisted across sessions so the File menu can present `Recent Configurations`
/ `Recent Watchlists` cascades.

## Public API

- `MAX_RECENT: int = 8` — cap on entries per kind.
- `list_recent(kind: str) -> List[str]` — newest-first list of stored paths.
  Unknown kinds return `[]`. Paths are NOT validated against the filesystem
  at read time (the load handler is the canonical validation point and is
  expected to call `remove_recent` on failure).
- `push_recent(kind: str, path: Any) -> List[str]` — record a fresh pick.
  Normalises via `Path(path).resolve(strict=False)`, case-sensitive de-dupes
  string-equal entries (falling back to `str(path)` if resolve raises),
  prepends to head, trims past `MAX_RECENT`. Returns the new list.
- `remove_recent(kind: str, path: Any) -> List[str]` — drop a specific path
  (used by load handlers when file is missing/malformed). Returns pruned list.
- `clear_recent(kind: Optional[str] = None) -> None` — drop one kind, or all
  kinds when `kind is None`.
- `display_label(path: str, *, max_len: int = 60) -> str` — menu-friendly
  label. Filename + parent if under budget; else `"…" + parent[-budget:] +
  "\\" + name`. Pure, no I/O.

Conventional kinds (consumed by `app.py`): `"configs"`, `"watchlists"`.
Unknown kinds are accepted and preserved on write (forward-compat).

## Storage

- File: `<app_data>/recent_files.json` (path via `paths.app_data_dir()`).
- Format: `{"configs": [...], "watchlists": [...]}` — values are absolute path
  strings.
- Atomic write via `core.io_helpers.atomic_write_json` (tmp + fsync +
  `os.replace`).
- Corrupt / unreadable file → empty state, silently overwritten on next push.
- All I/O is best-effort: any disk error swallowed — a failing MRU must not
  block File → Save Configuration….
- Unknown kinds in the on-disk file are preserved on write (so a newer build's
  e.g. "layouts" slot survives an older build's push), but only when their
  value is a `List[str]`.

## Dependencies

- Internal: `paths.app_data_dir`, `core.io_helpers.atomic_write_json`.
- External: stdlib only.

## Design notes

- LRU (not chronological) — a user alternating between two configs keeps both
  at top.
- No filesystem validation on read (slow network shares; user may remount
  between launch and click).
- Two independent kinds (not one combined list) so cascades stay focused.

## Invariants

- `list_recent(kind)` length `<= MAX_RECENT`.
- `list_recent(kind)` has no duplicate string-equal entries.
- `push_recent(kind, p)` then `list_recent(kind)[0]` equals the normalised
  string for `p`.
- `clear_recent(kind)` then `list_recent(kind)` returns `[]`.
- Corrupt `recent_files.json` (truncated, non-JSON, non-dict) silently treated
  as empty state on next read.

## Wiring (in `app.py`)

`ChartApp._build_menubar` appends two `tk.Menu` cascades (`Recent
Configurations`, `Recent Watchlists`). Each cascade's `postcommand` calls
`_refresh_recent_menu(menu, kind)` which rebuilds entries from `list_recent`
on every open. Each load/save handler (`_on_menu_load_config`,
`_on_menu_save_config(_as)`, `_on_menu_load_watchlists`,
`_on_menu_save_watchlists(_as)`) calls `push_recent(kind, path)` after a
successful op. Each cascade ends with a `Clear List` entry calling
`clear_recent(kind)`.
