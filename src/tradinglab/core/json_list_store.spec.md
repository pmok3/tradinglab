# core/json_list_store.py — Spec

## Purpose

Generic single-file JSON store for the "list-with-versioned-envelope"
on-disk shape:

```
{
  "schema_version": N,
  "<items_key>": [ <record>, <record>, ... ],
  "<optional extras>": <opaque>,
  ...
}
```

Sibling to :class:`core.json_collection_store.JsonObjectStore`, which
handles the "one file per record + `_index.json`" shape. The two cover
the two persistence morphologies used across the codebase; pick one
per subsystem.

| Pattern                         | Use                                  |
|---------------------------------|--------------------------------------|
| One file per record + index     | `JsonObjectStore[T]` (entries, exits, scanner, …) |
| Single file, list w/ envelope   | `JsonListStore[T]` (positions/open.json, watchlists) |

`positions/storage.py` is the pilot migration; `watchlists/storage.py`
is the future migration target (deferred — its `pinned` extras carry
ordering + UI semantics that need their own migration sprint).

## Public surface

```python
class JsonListStore(Generic[T]):
    def __init__(
        self, *,
        path: Callable[[], Path],
        items_key: str,
        to_dict:   Callable[[T], dict],
        from_dict: Callable[[dict], T],
        schema_version: int = 1,
        migrate: Callable[[dict, int], dict] | None = None,
        kind_label: str = "list-store",
        extra_keys: tuple[str, ...] = (),
    ): ...

    def path_for(self, *, root=None) -> Path
    def load(self, *, root=None) -> list[T]
    def load_with_extras(self, *, root=None) -> tuple[list[T], dict[str, Any]]
    def save(self, items, *, root=None) -> Path
    def save_with_extras(self, items, extras, *, root=None) -> Path
    def clear(self, *, root=None) -> bool
```

Every method accepts an optional `root: Path | None` so tests can
sandbox writes to `tmp_path` without monkey-patching the `path`
callable (mirrors `JsonObjectStore[T]`). When `root` is supplied the
file is resolved as `root / path().name`.

## Contract

- **`load`** is lenient: returns `[]` on failure modes (missing file,
  unreadable, non-object root, invalid `schema_version`, refused
  future version, migration failure). A missing `schema_version`
  defaults to version 1 for backward compatibility. Individual records
  that fail `from_dict` are skipped + logged at WARNING; the rest of
  the list still loads.
- **Envelope-version refuse:** an on-disk `schema_version` STRICTLY
  GREATER than `self.schema_version` returns `[]` + WARNING. A
  lower-or-equal version is accepted; lower values trigger the
  `migrate` hook when supplied.
- **`migrate` hook semantics:** called with
  `(envelope_dict, on_disk_version)` whenever on-disk is older than
  current. Must return a dict shaped like the current envelope
  (i.e. with `items_key` and any `extra_keys` present). Exceptions
  raised by the hook are caught + logged; the load degrades to `[]`
  rather than propagating. Default: identity (no-op).
- **`save`** writes the whole envelope atomically via
  `core.io_helpers.atomic_write_json`. Always stamps the envelope with
  the store's current `schema_version`.
- **`save_with_extras(items, extras)`** writes the envelope including
  the supplied extras dict. Extras keys MUST NOT collide with
  `schema_version` or `items_key` (raises `ValueError` if they do).
  Extras whose key is NOT declared in `extra_keys` are still written
  through — `extra_keys` only governs what `load_with_extras` reads
  back. Values are opaque: the store never inspects shape.
- **`load_with_extras`** returns `(items, extras_dict)` where
  `extras_dict` has exactly the declared `extra_keys` (each either
  pulled from the envelope or `None` if missing). On the empty/refused
  path, every extras value is `None`.
- **`clear`** unlinks the file; returns `True` iff a file existed
  (mirrors `clear_trail_state` semantics from positions storage).

## "Extras" rationale

Some real-world envelopes carry sibling top-level keys alongside the
items list (e.g. watchlists' `pinned: list[str]`). Two options were
considered:

1. Force every additional top-level key to have its own sibling type.
   → Code blow-up; the store would need 4+ generics for watchlists.
2. **Pass extras through opaquely.** ← chosen.

The store treats extras as `Any`. Callers that need typed extras can
wrap `load_with_extras` and validate the dict locally. This keeps the
generic small and matches how `JsonObjectStore`'s `BrokenRecord`
preserves raw text without trying to type it.

## Dependencies

Stdlib only + `core.io_helpers.atomic_write_json` + `read_json`. No
subsystem imports — the generic must stay reusable.

## Logging

`logging.getLogger(__name__)` emits one **WARNING** per failure
(missing file is silent; that's a normal first-run condition). Bad
envelope, future schema, migration failure, malformed individual
record — all WARNING, never ERROR.

## Migration status

- `positions/storage.py` open-positions list — migrated (pilot).
- `watchlists/storage.py` — deferred. Its `pinned` extras need an
  ordered-list-preserving migration that's out of scope for this
  sprint; the `extra_keys` machinery here is forward-compatible for it.

## Tests

`tests/core/test_json_list_store.py` covers: empty/missing → `[]`,
save round-trip, envelope shape, bad envelope + WARNING, future
version refuse + WARNING, migrate hook transforms older versions,
extras pass-through round-trip, `clear` on existing vs missing file.
