# entries/storage.py — Spec

## Purpose

JSON persistence for entry strategies. One file per strategy at
`<cache_dir>/entry_strategies/<id>.json` with an `_index.json` for
fast listing. Atomic writes, lenient bulk load with `BrokenStrategy`
records preserved for GUI recovery.

## Layout

```
<cache_dir>/entry_strategies/
  ├─ 0a1b2c3d-….json     ← one file per EntryStrategy.id
  ├─ tmpl-scan-…json     ← bundled starter-pack templates
  └─ _index.json         ← id -> name map for fast listing
```

## Public API

- `storage_dir() -> Path` — `<cache_dir>/entry_strategies/`, created
  if missing.
- `@dataclass BrokenStrategy(path, error, raw_json)` — record from
  `load_all` for files that fail to parse / validate. Raw JSON
  preserved so the GUI can render Recover/Delete.
- `save(strategy, *, root=None) -> Path` — atomic write; refreshes
  `_index.json`. Validation via `validate_strategy` refuses invalid
  drafts.
- `load(strategy_id, *, root=None) -> EntryStrategy` — strict
  per-file load (raises on missing / corrupt / future-schema).
- `load_all(*, root=None) -> Tuple[List[EntryStrategy], List[BrokenStrategy]]`
  — lenient bulk load. Sorted by `(name.lower(), id)`.
- `delete(strategy_id, *, root=None) -> bool`.
- `export_to_path(strategy, dst_path) -> Path`.
- `import_from_path(src_path, *, on_collision=...) -> EntryStrategy`.

## Dependencies

- `..core.json_collection_store.JsonObjectStore` — generic
  implementation. This module is a thin wrapper around a
  module-level `_STORE` instance.
- `..core.io_helpers.atomic_write_json` — re-exported via `_STORE` but
  also kept as `_atomic_write_json` for any legacy import.
- `..disk_cache._cache_dir`.
- `.model.{EntryStrategy, validate_strategy, _new_id}`.

## Design Decisions

- **Delegates to the generic store.** The hand-rolled implementation
  (~150 LOC of try/except + path glue + index management) was retired
  in favour of `core.json_collection_store.JsonObjectStore`. Public
  function signatures and `BrokenStrategy` dataclass are preserved
  byte-for-byte — every existing test passes unmodified.
- **`BrokenStrategy` kept as a back-compat alias.** Callers that do
  `from tradinglab.entries.storage import BrokenStrategy` keep
  working. `load_all` repackages generic `BrokenRecord` instances
  into `BrokenStrategy` so isinstance checks still match.
- **Rename-on-import behavior stays subsystem-specific.** The generic
  store accepts a `rename_fn` callable; entries supplies
  `_rename_on_import` (new id + " (imported)" suffix) preserving the
  exact historical UX.
- **Index is best-effort.** `_load_index` returns `{}` on missing /
  corrupt; `load_all` falls back to directory scan. Never a
  correctness gate.
- **`sort_keys=True`** in `_atomic_write_json` — diff-friendly bytes.
- **Corrupt files don't crash `load_all`** — surface as
  `BrokenStrategy` with raw JSON.

## Invariants

- `save(s)` writes `schema_version = CURRENT_SCHEMA_VERSION`.
- `load_all()` sorted by `(name.lower(), id)`; ties broken by id.
- `schema_version > CURRENT_SCHEMA_VERSION` rejected loudly
  (`BrokenStrategy` in `load_all`, raises in `load`).

## See also

- Mirror: [`exits/storage.spec.md`](../exits/storage.spec.md).
