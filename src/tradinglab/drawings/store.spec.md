# `drawings/store.py` — drawing store + persistence (Feature C)

## Purpose

`DrawingStore` is the single source of truth for the user's drawings
during a session. It owns in-memory state, fires observer callbacks on
every mutation, and coalesces disk writes to one per Tk idle tick.

Module-level functions (`read_drawings`, `write_drawings`,
`clear_drawings`, `drawings_file_path`) isolate the persistence layer
so tests can exercise it without a store.

## Public API

```python
DRAWINGS_FILE_FORMAT  = "tradinglab-drawings"
DRAWINGS_FILE_VERSION = 1
DRAWINGS_FILE_NAME    = "drawings.json"

Subscriber = Callable[[str, Optional[str], Optional[Drawing]], None]
Scheduler  = Callable[[Callable[[], None]], None]

class DrawingStore:
    def __init__(self, *, scheduler=None, autosave=True): ...

    # subscribers
    def subscribe(self, cb) -> Callable[[], None]: ...

    # accessors
    def list(self, ticker) -> List[Drawing]: ...
    def all(self)  -> Dict[str, List[Drawing]]: ...
    def get(self, drawing_id) -> Optional[Tuple[str, Drawing]]: ...
    def tickers(self) -> List[str]: ...
    def __len__(self) -> int: ...
    def count(self, ticker) -> int: ...  # O(1) bucket size; no list copy
    def revision(self) -> int: ...       # mutation counter (pick-cache key)

    # mutation
    def add(self, drawing) -> Drawing: ...
    def remove(self, drawing_id) -> bool: ...
    def update(self, drawing_id, **changes) -> Optional[Drawing]: ...
    def clear_symbol(self, ticker) -> int: ...
    def clear_all(self) -> int: ...
    def replace_all(self, by_ticker) -> None: ...  # no save

    # persistence
    def flush(self) -> None: ...
    def subscribe_save_errors(self, cb) -> Callable[[], None]: ...

def drawings_file_path() -> Path: ...
def read_drawings()  -> Dict[str, List[Drawing]]: ...
def write_drawings(by_ticker) -> Optional[OSError]: ...  # None on success
def clear_drawings() -> None: ...
```

## On-disk format

```json
{
  "format": "tradinglab-drawings",
  "version": 1,
  "saved_at": "2026-04-30T12:34:56",
  "drawings_by_ticker": {
    "AMD": [ { "kind": "hline", "id": "...", "ticker": "AMD",
               "price": 92.50, "color": "#2962ff", "width": 1.0,
               "style": "solid", "label": "stop",
               "created_at": "...", "extra": {} } ]
  }
}
```

Location: `<app_data_dir>/drawings.json`, next to `settings.json`,
`watchlists.json`, `sandbox_last.json`. **Not** inside `cache/` —
drawings survive a candle-cache wipe.

## Subscriber events

| event           | ticker         | drawing  | when                              |
|-----------------|----------------|----------|-----------------------------------|
| `"add"`         | normalized key | `Drawing`| `.add(d)`                         |
| `"remove"`      | normalized key | `Drawing`| `.remove(id)` succeeds            |
| `"update"`      | new key        | `Drawing`| `.update(id, **changes)` succeeds |
| `"clear_symbol"`| normalized key | `None`   | `.clear_symbol(ticker)` non-empty |
| `"clear_all"`   | `None`         | `None`   | `.clear_all()` non-empty          |
| `"loaded"`      | `None`         | `None`   | `.replace_all(by_ticker)` finishes|

## Invariants

- **Atomic writes** (tempfile + `os.replace`). Tempfile is in the same
  directory as the target so rename is single-filesystem. A
  `try/finally` unlinks orphan tempfiles on AV-block / OneDrive-lock /
  disk-full failures.
- **Silent on OS errors at the API surface**, but `write_drawings`
  returns the captured `OSError` (or `None`). `flush()` dispatches it
  to subscribers registered via `subscribe_save_errors(cb)` so the app
  can surface disk-full / OneDrive-lock / AV-block failures.
- **Tolerant reads**. Missing file, malformed JSON, format/version
  mismatch, garbage per-drawing entries: each fails gracefully.
  Future-version files are preserved on disk (not deleted).
- **Future-version write/clear refusal**. `write_drawings` and
  `clear_drawings` peek the on-disk version via `_peek_file_version`.
  If on-disk version > `DRAWINGS_FILE_VERSION`, the write/unlink is
  silently skipped so an in-memory `{}` never trumps newer-format
  data. Missing/corrupt/foreign payloads are treated as "no future
  version" and write proceeds.
- **Canonical-key invariant**. `add` / `update` / `replace_all`
  rebucket drawings whose `ticker` field disagrees with the bucket
  key. `d.ticker == k` for every `d in self._by_ticker[k]`.
- **Unique ids**. `add` raises `ValueError` on duplicate id;
  `read_drawings` / `replace_all` silently drop subsequent duplicates
  (first wins). Lookups (`get`/`update`/`remove`) match by id;
  duplicates would be undeletable from the UI.
- **Main-thread mutation**. `add`, `remove`, `update`, `clear_symbol`,
  `clear_all`, `replace_all` are decorated `@require_tk_thread` and
  raise `TkThreadViolation` off-main — Python dict/list ops aren't
  atomic and autosave interleaves reads/writes of `_by_ticker`.
  Read-only methods (`list`, `get`, `__len__`, `tickers`, `all`,
  `subscribe`) are unchecked so background diagnostics can poll.
- **Coalesced persistence**. First mutation in a quiet period
  schedules one idle write; subsequent mutations share it. Pass
  `autosave=False` to drive flush explicitly in tests.
- **`replace_all` does NOT persist** — the data just came from disk.
- **Subscriber errors are swallowed** so one broken subscriber doesn't
  break the chain.
- **`revision()` counter**. Bumped FIRST in `_notify` on every event.
  Hover-rate consumers (`InteractionMixin._pick_drawing_at_event`) use
  it in their pick-result cache key to skip re-scans when neither the
  store nor cursor moved.
- **O(1) bucket count** (`count(ticker)`). Direct `len(dict.get(...))`
  for hover-rate callers that must skip the `list(ticker)` copy.
- **`clear_drawings()` notifies live stores**. Module-level
  `clear_drawings()` walks a `weakref.WeakSet` registry of live
  `DrawingStore` instances and calls `clear_all()` on each before
  unlinking the file — otherwise an in-memory store would re-persist
  the cleared data on the next mutation.

## Wiring (in `app.py`)

```python
self._drawings = DrawingStore(scheduler=self.after_idle)
self._drawings.replace_all(read_drawings())
self._drawings.subscribe(self._on_drawing_event)
self._drawings.subscribe_save_errors(self._on_drawing_save_error)

# On close:
try: self._drawings.flush()
except Exception: pass
```
