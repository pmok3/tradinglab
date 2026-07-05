# core/json_collection_store.py — Spec

## Purpose

Generic per-id JSON collection store. Hosts the shared implementation
of the storage pattern that was previously copy-pasted across six
subsystems (entries, exits, scanner, watchlists, strategy_tester,
positions). Each subsystem narrows the generic via a thin module-level
instance + delegating public wrappers.

## Layout

```
<storage_dir>/
  ├─ <id>.json       ← one file per object (id provided by id_of)
  └─ _index.json     ← {id: index_value} for fast listing
```

- Writes go through `core.io_helpers.atomic_write_json` with
  `sort_keys=True` — byte-stable diffs across resaves.
- `_index.json` is a best-effort cache: corrupt → ignored (logged at
  WARNING), bulk reads can rebuild via `refresh_index`.

## Public surface

```python
@dataclass
class BrokenRecord:
    path: Path
    error: str
    raw_json: str | None = None

class JsonObjectStore(Generic[T]):
    def __init__(
        self, *,
        storage_dir: Callable[[], Path],
        kind_label: str,
        to_dict:     Callable[[T], dict],
        from_dict:   Callable[[dict], T],
        id_of:       Callable[[T], str],
        validate:    Callable[[T], None] | None = None,
        index_value_of: Callable[[T], str] | None = None,
        index_filename: str = "_index.json",
    ): ...

    def path_for(self, obj_id, *, root=None) -> Path
    def index_path(self, *, root=None) -> Path
    def load_index(self, *, root=None) -> dict[str, str]
    def save_index(self, index, *, root=None) -> None
    def refresh_index(self, *, root=None) -> dict[str, str]
    def list_ids(self, *, root=None) -> list[str]
    def save(self, obj, *, root=None) -> Path
    def load(self, obj_id, *, root=None) -> T
    def delete(self, obj_id, *, root=None) -> bool
    def load_all(self, *, root=None) -> tuple[list[T], list[BrokenRecord]]
    def export_to_path(self, obj, dst) -> Path
    def import_from_path(
        self, src, *, root=None,
        on_id_collision="rename",
        rename_fn: Callable[[T], T] | None = None,
    ) -> T
```

Every method accepts an optional `root: Path | None` so tests can
sandbox writes to `tmp_path` without monkey-patching the
`storage_dir` callable.

## Contract

- **`save`** runs `validate` first (if provided) — invalid objects
  never hit disk. Refreshes `_index.json` entry for the saved id.
- **`load`** is strict: missing file → `FileNotFoundError`; malformed
  JSON → `ValueError` (wrapping `json.JSONDecodeError`). Validation
  is NOT re-run on per-id loads (callers can re-validate if needed).
- **`load_all`** is lenient: every file that fails to read / parse /
  validate becomes a `BrokenRecord`. Raw text is preserved for
  object-parser and validation failures; unreadable files and malformed
  JSON report `raw_json=None`. The bulk read never crashes the caller.
  Sorted by filename (= id) for deterministic ordering.
- **`delete`** is idempotent: returns `True` only when a file was
  actually removed, but always prunes the index entry.
- **`refresh_index`** swallows per-file errors (logged at WARNING)
  so a single corrupt JSON cannot block the refresh.
- **`import_from_path`** validates the imported object then delegates
  to `save`. Collision policy follows the historical entries / exits
  semantics:
  - `"rename"` → call `rename_fn(obj)` (must be supplied) and save the
    mutated copy. Subsystem-level rename_fn typically mints a new id
    and decorates the display name.
  - `"overwrite"` → replace the existing file.
  - `"reject"` → raise `ValueError`.

## Dependencies

Stdlib only + `core.io_helpers.atomic_write_json`. Crucially, no
subsystem imports — the generic must stay reusable.

## Logging

`logging.getLogger(__name__)` emits one **WARNING** per broken
record / index corruption / refresh skip. Broken records are an
expected occasional artifact (user-edited JSON, half-written file
from a crash) — they are not ERROR.

## Migration status

- `entries/storage.py` — migrated (pilot).
- `exits/storage.py`, `scanner/storage.py` — partially migrated; custom
  `save` / `load_all` behaviour remains hand-rolled.
- `watchlists/storage.py`, `strategy_tester/storage.py` — still
  hand-rolled; incompatible envelope / directory layouts are deferred.
- `positions/storage.py` — uses `JsonListStore`, not this per-id store.

## Tests

`tests/core/test_json_collection_store.py` exercises the generic
contract end-to-end with a synthetic `Item` dataclass:
save round-trip, missing → FileNotFoundError, malformed → ValueError
+ BrokenRecord, mixed good/broken triage, delete semantics, index
refresh tolerance, import/export round-trip, collision policies.
