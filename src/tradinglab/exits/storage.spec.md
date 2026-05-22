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
Anything else is ignored.

## Public API

```python
def storage_dir() -> Path

@dataclass
class BrokenStrategy:
    path: Path
    error: str
    raw_json: str

class CollisionDecision(str, Enum):
    OVERWRITE = "overwrite"
    RENAME    = "rename"
    CANCEL    = "cancel"

def save(strategy: ExitStrategy, *, root=None) -> Path
def load(strategy_id, *, root=None) -> ExitStrategy
def load_all(*, root=None) -> Tuple[List[ExitStrategy], List[BrokenStrategy]]
def delete(strategy_id, *, root=None) -> bool
def find_by_name(name, *, root=None) -> Optional[ExitStrategy]
def export_strategy(strategy, dst_path: Path) -> Path
def import_strategy(src_path, *,
                    on_collision: Callable[[ExitStrategy, ExitStrategy],
                                           CollisionDecision]
                    ) -> Optional[ExitStrategy]
```

## Dependencies

- `..core.io_helpers.atomic_write_json`.
- `..disk_cache._cache_dir`.
- `.model.{ExitStrategy, validate_strategy}`.

## Design Decisions

- **No `_index.json`** for exits (unlike entries / scanner). Strategy
  counts are O(10–50); index optimisation isn't worth the consistency
  cost.
- **`load_all` is lenient.** Corrupt / future-schema files surface as
  `BrokenStrategy` with raw JSON preserved.
- **Atomic writes via `atomic_write_json`** — tmp-file + rename +
  fsync; safe under hard kill.
- **`sort_keys=True`** — re-saving identical content yields identical
  bytes (diff-friendly).
- **`import_strategy` rolls a fresh UUID** on RENAME; OVERWRITE
  replaces; CANCEL no-ops.
- **`tmpl-*.json` is a first-class id namespace** — templates load
  alongside user strategies; NOT editable in-place (dialog clones
  into a new UUID on Save).

## Invariants

- `save(s)` writes `schema_version = CURRENT_SCHEMA_VERSION`.
- `load_all()` returns strategies sorted by `(name.lower(), id)`.
- A file with `schema_version > CURRENT_SCHEMA_VERSION` becomes a
  `BrokenStrategy` in `load_all`; raises in `load`.

## See also

- Mirror: [`../entries/storage.spec.md`](../entries/storage.spec.md).
