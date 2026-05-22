# settings.py — Spec

## Purpose

In-memory configuration store with **explicit** JSON file import/export. No
auto-persisting; users load a config via `File → Load Configuration…` and
write back via `File → Save Configuration…` (text-editor model).

Replaces the previous on-disk auto-persist model (no `settings.json` is
ever auto-created in `%LOCALAPPDATA%`).

## Public API

- `load() -> dict` — shallow copy of the in-memory store.
- `save(d: dict) -> None` — replace the store wholesale (compat).
- `get(key, default=None) -> Any` — in-memory lookup.
- `set(key, value) -> None` — write to memory, marks dirty.
- `clear() -> None` — wipe store + dirty + loaded_path.
- `import_from_file(path) -> bool` — read JSON, replace store, strip `_`-prefixed keys, reset dirty, set loaded_path. Returns False on missing / malformed / non-dict payload (state untouched on failure).
- `export_to_file(path, *, include_comments=False) -> bool` — atomic write via shared `core.io_helpers.atomic_write_json` (tmp + fsync + `os.replace`). Resets dirty + sets loaded_path on success. Strips `_`-prefixed keys by default.
- `loaded_path() -> Path | None` — most recently loaded/exported file.
- `is_dirty() -> bool` — True if mutations occurred since last load/export.

## Dependencies

- External: `json`, `pathlib`. **No** dependency on `disk_cache` anymore.

## Design notes

- No auto-persist. `set()` only mutates memory and flips a dirty bit; user
  must explicitly Save Configuration… (text-editor save model).
- Comment keys: any key starting with `_` (e.g. `_comment`, `_comment_<key>`)
  is stripped on import and (by default) on export. `include_comments=True`
  on export preserves them; only the example-config generator uses this.
- No schema versioning — new keys land via `defaults.TUNABLES`; unknown
  non-`_`-prefixed keys are preserved on round-trip.
- Atomic writes via `core.io_helpers.atomic_write_json`.
- All file I/O failures return `False` rather than raising.

## Invariants

- `import_from_file` failure leaves `_store`, `_loaded_path`, `_dirty` unchanged.
- After `export_to_file(p)`: `loaded_path() == p` and `is_dirty() is False`.
- Any key starting with `_` never reaches the application layer (filtered on import).

## Known keys (curated user-facing subset)

See `defaults.spec.md` and `defaults.TUNABLES` for the authoritative list.
The seven keys exported into `config/example_config.json`:

- `display_tz` (str)
- `scroll_zoom_invert` (bool)
- `theme_overrides` (dict, sparse per-mode palette)
- `startup_defaults` (dict, sparse ticker/compare/interval/source/theme)
- `default_window_bars` (int, 10..5000)
- `price_top_pad_frac` (float, 0..1)
- `price_bot_pad_frac` (float, 0..1)
- `local_data` (dict, BYOD config: `{"enabled": bool, "roots": [{"name": str, "path": str}, ...]}` — see `data/local_source.spec.md`)

Internal perf knobs (`full_cache_size`, `hover_throttle_ms`, the
`scroll_zoom_*` triple) are tagged `is_user_facing=False` and excluded from
the example config, but advanced users can still set them in a hand-edited
config file.
