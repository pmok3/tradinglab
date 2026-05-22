# scanner/storage.py — spec

## Purpose

Per-scan JSON persistence under `<cache_dir>/scans/`. One file per
scan, UUID4-keyed filename, atomic writes, lenient bulk load.

## Layout

```
<cache_dir>/scans/
  ├─ 0a1b2c3d-….json     ← one file per ScanDefinition.id
  ├─ ….json
  └─ _index.json          ← reserved for v1.1 fast-listing (not yet written)
```

`<cache_dir>` resolves via `disk_cache._cache_dir()` so all scanner
state shares the user's cache root. Tests monkeypatch to a tmp dir.

Filename pattern: `^([0-9a-fA-F-]{8,}|tmpl[A-Za-z0-9_-]*)\.json$`.
Non-matching files are ignored by `load_all` (e.g. `_index.json`,
backups, OS metadata). The `tmpl*` branch lets bundled starter-pack
templates (`tradinglab.templates`) use human-readable ids like
`tmpl-scan-rvol-hi` rather than synthetic UUIDs.

## Public API

- `scans_dir() -> Path` — `<cache_dir>/scans`, created if missing.
- `scan_path(scan_id) -> Path` — `scans_dir() / f"{scan_id}.json"`.
- `save(scan: ScanDefinition) -> Path` — atomic write, returns dest.
- `load(scan_id) -> ScanDefinition` — strict (raises on missing /
  corrupt / future-schema).
- `load_all() -> List[ScanDefinition]` — lenient bulk load. Skips
  corrupt files with a warning. Sorted by `(name.lower(), id)`.
- `delete(scan_id) -> bool` — True if removed, False if absent.
- `find_by_name(name) -> Optional[ScanDefinition]` — case-insensitive.
- `export_scan(scan, dst_path) -> Path` — portable copy at arbitrary
  path (GUI Export dialog).
- `import_scan(src_path, *, on_collision=None) -> ScanDefinition` —
  with id/name collision callbacks.

## Atomic write

`_atomic_write_json(path, payload)` is a thin shim over
`tradinglab.core.io_helpers.atomic_write_json`: `tempfile.mkstemp` in
parent dir → `json.dump` + `flush` + `fsync` (best-effort) →
`os.replace` (atomic on POSIX & Windows). On any exception,
`os.unlink(tmp)` (best-effort). Readers never see a half-written file.

## Import collisions

`CollisionDecision = OVERWRITE | RENAME | CANCEL`. Two-phase:

1. **ID collision** (existing file has same `scan.id`):
   - `OVERWRITE` → save incoming as-is.
   - `RENAME` → mint new UUID + unique name based on incoming, save.
   - `CANCEL` → return `None`, no side effects.

2. **Name collision** (different ID, same name):
   - `OVERWRITE` → keep the **local** id (so any open Scanner sub-tab
     keeps working), unlink the incoming file, save with `merged.id =
     name_clash.id`. Incoming `id` is intentionally discarded.
   - `RENAME` → mint unique name, save with incoming id intact.
   - `CANCEL` → return `None`.

**Default callback is CANCEL.** A caller without `on_collision` gets
safe behavior — never silently overwriting.

## Schema versioning

`save` writes `schema_version = SCHEMA_VERSION`. `load_all` →
`load` → `ScanDefinition.from_dict` → `model.migrate(d,
from_version)`. Loud refusal on `schema_version > SCHEMA_VERSION`.

## What we *don't* do here

- Migrate older versions silently — `model.migrate`.
- Validate semantic correctness — `engine.validate_scan`.
- Lock files (single-process, main-thread writes only).
- Backup-on-overwrite (power users use Export).

## See also

- [model](model.spec.md), [runner](runner.spec.md).
- App save/delete callbacks: [`app.spec.md`](../app.spec.md) §"Scanner tab integration".
