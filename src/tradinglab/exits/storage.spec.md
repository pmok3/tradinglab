# exits/storage.py — Spec

## Purpose

JSON persistence for exit strategies. One file per strategy at
`<cache_dir>/exit_strategies/<id>.json` plus bundled `tmpl-*.json`
templates. Atomic writes, lenient bulk load with `BrokenStrategy`
records preserved for GUI Recover/Delete UX.

## Layout

```
<cache_dir>/exit_strategies/
  ├─ 0a1b2c3d-….json    ← one file per ExitStrategy.id
  ├─ tmpl-bracket-….json ← bundled starter-pack templates
  └─ (no _index.json — directory scan is cheap enough)
```

Filename regex: `^([0-9a-fA-F-]{8,}|tmpl[A-Za-z0-9_-]*)\.json$`.
Anything else is ignored by `load_all`.

## Public API

```python
def exit_strategies_dir() -> Path
def strategy_path(strategy_id: str) -> Path

@dataclass
class BrokenStrategy:
    id: str
    name: str
    reason: str
    raw_json: dict   # NOT str — exits keeps the parsed dict, not raw text

class CollisionDecision(str, Enum):
    OVERWRITE = "overwrite"
    RENAME    = "rename"
    CANCEL    = "cancel"

def save(strategy: ExitStrategy) -> Path
def load(strategy_id) -> ExitStrategy
def load_all() -> Tuple[List[ExitStrategy], List[BrokenStrategy]]
def delete(strategy_id) -> bool
def find_by_name(name) -> Optional[ExitStrategy]
def export_strategy(strategy, dst_path: Path) -> Path
def import_strategy(src_path, *,
                    on_collision: Callable[[ExitStrategy, ExitStrategy],
                                           CollisionDecision]
                    ) -> Optional[ExitStrategy]
```

## Dependencies

- `..core.json_collection_store.JsonObjectStore` — used as a helper for
  `path_for` / `load` (via `_STORE.delete`, `_STORE.export_to_path`,
  `_STORE.path_for`). Does **not** drive the `save` / `load_all` paths
  (see Design Decisions).
- `..core.io_helpers.atomic_write_json` — direct use in `save` to avoid
  the index side-effect of `JsonObjectStore.save`.
- `..disk_cache._cache_dir`.
- `.model.{ExitStrategy, validate_strategy, CURRENT_SCHEMA_VERSION}`.

## Design Decisions

- **Partial delegation to the generic store.** `path_for`, `delete`
  and `export_to_path` route through a module-level
  `_STORE: JsonObjectStore[ExitStrategy]`. `save`, `load`, `load_all`
  and `import_strategy` remain bespoke because exits has a few
  hard-coded constraints that diverge from the generic:
  1. **No `_index.json`** — the test
     `test_save_atomic_no_temp_files_left` asserts only the
     single UUID file exists after `save()`. The generic's
     `save()` would also emit `_index.json`. We therefore write
     via `atomic_write_json` directly.
  2. **Unparseable files are LOG-only, not broken** — the test
     `test_load_all_skips_unparseable_files` asserts `broken == []`
     when the file fails JSON parse. The generic includes them in
     `broken` with `raw_json=None`.
  3. **`BrokenStrategy.raw_json` is a `dict`**, not `str` — exits
     callers (GUI Recover dialog) inspect parsed fields.
  4. **Filename regex** — exits only considers files matching
     `_FILENAME_RE`; the generic considers all `*.json`.
  5. **Schema-version-too-new rejection** — `_from_raw` raises
     `ValueError` for `schema_version > CURRENT_SCHEMA_VERSION`,
     guarding both `load` and `load_all`.
- **`delete` is safe under the no-index policy** — the generic's
  `delete` reads `_index.json` (returns `{}` on miss) and only
  writes it back if the id was present. Since we never create the
  file, `load_index()` is always `{}` and `save_index()` is never
  called. The exit-file unlink path is identical.
- **`CollisionDecision` enum + two-tier collision in
  `import_strategy`** — id-collision first, then name-collision
  with a different UUID. OVERWRITE-by-name keeps the local id so
  open positions referencing the strategy keep working. This is
  more elaborate than the generic's single `on_id_collision` knob
  and stays subsystem-specific.
- **Atomic writes via `atomic_write_json`** — tmp-file + rename +
  fsync; safe under hard kill.
- **`tmpl-*.json` is a first-class id namespace** — templates load
  alongside user strategies; NOT editable in-place (dialog clones
  into a new UUID on Save).

## Invariants

- `save(s)` writes `schema_version = CURRENT_SCHEMA_VERSION`.
- `load_all()` returns strategies sorted by `(name.lower(), id)`.
- A file with `schema_version > CURRENT_SCHEMA_VERSION` becomes a
  `BrokenStrategy` in `load_all`; raises in `load`.
- `save` writes exactly one file per call — no `_index.json` side
  effect.

## See also

- Mirror: [`../entries/storage.spec.md`](../entries/storage.spec.md).
- Generic backend: [`../core/json_collection_store.spec.md`](../core/json_collection_store.spec.md).
