# scanner/tick_source.py — spec

## Purpose

Tick source abstraction that decouples *where ticks come from* from
*what consumes them* (`ScanRunner`, future `ExitEvaluator` /
`EntryEvaluator`). A small `Protocol` + concrete adapters so a
sandbox replay, a polling HTTP fetch, a websocket feed, or a fake
test clock can all drive the same downstream pipeline.

## Public API

```python
@dataclass(frozen=True)
class Tick:
    tick_id: int                                   # monotonic per-source
    candles_by_symbol: Mapping[str, List[Candle]]  # NOT copied — see notes
    forming: bool                                  # last bar provisional?
    timestamp: datetime                            # source's notion of "now"

TickCallback = Callable[[Tick], None]

class TickSource(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def subscribe(self, callback: TickCallback) -> None: ...
    def latest_candles_by_symbol(self) -> Mapping[str, List[Candle]]: ...

class PollingTickSource:
    def __init__(self, fetch_fn, symbols, *, interval_s=1.0,
                 forming=False, clock=utc_now) -> None: ...
    def start(self) / stop(self) / subscribe(cb) / unsubscribe(cb): ...
    def set_symbols(self, symbols) -> None: ...
    def latest_candles_by_symbol(self) -> Mapping[str, List[Candle]]: ...

class QueuedTickSource:
    """Thread-boundary buffer (upstream → bounded queue → consumer)."""
    def __init__(self, upstream: TickSource, *, maxsize: int = 0): ...
    def start(self) / stop(self): ...
    def subscribe(self, callback) -> None  # bypass queue
    def drain(self, timeout=None) -> Optional[Tick]
    def drain_all(self) -> List[Tick]
    @property
    def dropped(self) -> int
    @property
    def pending(self) -> int
```

## Dependencies

- `..models.Candle`. `threading`, `queue`, `datetime`, `logging` —
  std lib only. No Tk, no scanner internals.

## Design

- **No `runner.run()` call here.** Sources emit `Tick`s; a separate
  dispatcher (or the GUI's tick hook) translates a tick into a runner
  invocation. Decouples from the runner signature.
- **Subscriber callbacks may run on background threads.** Concrete
  sources isolate subscriber exceptions so a bad subscriber cannot
  kill the source loop.
- **`QueuedTickSource` is the canonical Tk-marshalling shim.** Bounded
  `maxsize > 0` drops oldest on overflow (logged). `drain*` is
  non-blocking by default; typical Tk pattern is a 50 ms `after()`.
- **`candles_by_symbol` is NOT defensively copied** inside `Tick`.
  In-place-mutating sources must serialize or emit snapshots before
  constructing a tick.
- **`PollingTickSource` snapshots upstream data** before dispatching.
  `QueuedTickSource` buffers `Tick` objects from its upstream; it does
  not deep-copy their candle lists.

## Invariants

- `tick_id` is strictly increasing within one source instance.
- `subscribe(cb)` delivers every tick emitted after subscription, in
  source-thread order.
- `stop()` is idempotent; subsequent `start()` resumes from the next
  `tick_id` (counter not reset).
- `QueuedTickSource.dropped` only increments when `maxsize > 0`.
