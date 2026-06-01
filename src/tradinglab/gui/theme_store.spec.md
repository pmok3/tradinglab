# `gui/theme_store.py` — Spec

## Purpose

JSON-per-file persistence for **user-saved colour themes**, accessed
from `gui/theme_editor.py`. Lets the user capture the current theme
override state under a name, re-apply / delete it later. Storage is
deliberately one file per theme so themes:

- survive uninstall/reinstall (live next to `settings.json` under
  the per-user app-data dir);
- are trivial to back up / share (copy one `.json` file);
- can be hand-edited without breaking the rest of the user's themes
  (a corrupt file logs a warning and is skipped — `load_all` keeps
  returning every other valid theme).

## Public API

- `class UserTheme(label: str, mode: str, overrides: dict)` —
  frozen dataclass. `__post_init__` validates `mode in {"light",
  "dark"}` (raises `ValueError`) and filters `overrides` to
  `CUSTOMIZABLE_THEME_KEYS` (drops unknown keys + non-string
  values), so downstream merges always see a safe dict.
  Round-trips via `to_dict()` / `from_dict(data)`.
- `themes_dir() -> Path` — lazy resolver for the storage directory
  (`<app_data_dir>/themes/`). Lazy because tests monkeypatch the
  function to redirect at a `tmp_path`.
- `save_theme(theme: UserTheme) -> Path` — write atomically via
  `core.io_helpers.atomic_write_json`. Overwrites any existing
  file with the same slug (same-label save = overwrite UX).
- `theme_exists(label: str) -> bool` — predicate, used by the
  Theme Editor to gate the overwrite-confirm prompt.
- `delete_theme(label: str) -> bool` — `unlink` the matching file;
  returns `True` iff a file was removed.
- `load_all() -> list[UserTheme]` — every saved theme, alphabetical
  by label (case-insensitive). Corrupt JSON / missing fields log a
  warning and skip the file — one bad file never blocks the rest.

## On-disk shape

```json
{
  "label": "My Custom Dark",
  "mode":  "dark",
  "overrides": {
    "win_bg":      "#202020",
    "ax_bg":       "#303030",
    "text":        "#f0f0f0",
    "grid":        "#555555",
    "bull_row_bg": "#114433",
    "bear_row_bg": "#441111"
  }
}
```

Filename is the slugified label (spaces → `_`, everything outside
`[A-Za-z0-9_-]` dropped, empty result falls back to `"theme"`).
Path traversal patterns like `../etc/passwd` collapse to
`etcpasswd` since `.` is intentionally excluded from the safe set.

## Slugification rules

`_slugify_label(label) -> str`:

1. Strip leading / trailing whitespace.
2. Replace runs of whitespace with `_`.
3. Drop everything outside `[A-Za-z0-9_-]` (NB: dots are excluded
   too — they're filesystem-valid but allowing them enables labels
   like `../etc/passwd` to round-trip into confusing
   `..etc..passwd` filenames, plus it's a path-traversal smell).
4. Fall back to literal `"theme"` if everything was stripped.

Two themes whose labels slugify to the same filename are NOT
supported — the second `save_theme` call overwrites the first
(matching the "save = overwrite" UX users already expect from the
built-in preset Save-and-Close pattern).

## Dependencies

- Internal: `..constants.CUSTOMIZABLE_THEME_KEYS`,
  `..core.io_helpers.atomic_write_json`,
  `..core.io_helpers.read_json`, `..paths.app_data_dir`.
- External: `dataclasses`, `json`, `logging`, `re`, `pathlib.Path`.

## Tests

Pinned by `tests/unit/gui/test_theme_store.py` (14 tests):

- `UserTheme` dataclass construction + mode validation + override
  key filtering.
- `save_theme` / `load_all` round-trip preserves label, mode,
  overrides.
- `load_all` returns alphabetical order.
- `save_theme` overwrites on same label.
- `delete_theme` returns False for non-existent label.
- Corrupt JSON file is skipped with a logged warning; a sibling
  valid theme still loads.
- Missing required field is skipped (load_all keeps the rest).
- `_slugify_label` handles whitespace, drops unsafe characters,
  falls back to `"theme"` on fully-stripped input.
- `themes_dir()` returns a writable path ending in `themes`.

## Invariants

- `UserTheme.overrides` only contains keys from
  `CUSTOMIZABLE_THEME_KEYS`; non-string values are dropped on
  construction.
- `UserTheme.mode` is always `"light"` or `"dark"`.
- `load_all()` is total — corrupt or missing fields skip the
  affected file but never raise.
- `save_theme` is atomic via `atomic_write_json` (no half-written
  files visible to a concurrent reader).
- `delete_theme` is idempotent — calling on a non-existent label
  returns `False`, never raises.
