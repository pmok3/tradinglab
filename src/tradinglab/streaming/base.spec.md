# streaming/base.py — Spec

## Purpose
Declares the `StreamSource` protocol and the `STREAM_SOURCES` registry. A stream source emits **tick** (in-progress bar changed — replace rightmost) or **rollover** (new bar — append) events for a `(ticker, interval)` subscription.

## Public API
- `EventKind = str` (alias; one of `"tick"`, `"rollover"`).
- `StreamCallback = Callable[[EventKind, Candle], None]`.
- `class StreamSource(Protocol)`: `subscribe(ticker, interval, on_event) -> unsubscribe: Callable[[], None]`.
- `STREAM_SOURCES: Dict[str, StreamSource]`.
- `register_stream(name, source)`.

## Dependencies
- Internal: `..models.Candle` (type).
- External: `typing`.

## Design Decisions
- **Subscription returns an unsubscribe callable**, not a subscription object. Minimal API surface, easy to stash in a list and clean up.
- **Real vs simulated must be self-documented** — Implementations MUST document in their own spec whether they emit real market ticks or simulated ones. The UI surfaces stream source by name; check the source's `__doc__` and spec before relying on the data.
- **`STREAM_SOURCES` registration is import-time only** — The registry is populated when the streaming package is imported. Runtime registration of new sources is not supported.
- **Contract: at most one trailing event may still complete after unsubscribe** (a caller-visible race with the source's own thread). Consumers must be idempotent to a single trailing callback. The app's token-gating path (`_stream_token` stamped into every event) makes this explicit.
- **Callbacks may fire from any thread** — the consumer is responsible for marshalling. Callback implementations MUST be thread-safe or post to a thread-safe queue. `ChartApp._start_stream_if_applicable` installs a closure callback that enqueues events into `self._stream_queue` (a thread-safe `queue.Queue`); `ChartApp._drain_stream_queue` pulls them on the Tk main thread via `after()`.
- **Two event kinds, not more**: anything else (open/close/seal) can be derived from the tick/rollover pair without growing the protocol.

## Invariants
- After `unsubscribe()` returns, at most one more callback may fire (never more).
- `STREAM_SOURCES` is idempotent on re-registration.

## Testing
- `check_90_streaming_dispatch`: subscribe → event dispatch → unsubscribe.
- `check_95_stream_queue_coalescing`: several rapid ticks coalesce into one redraw.

## Known limitations
- No built-in backpressure. If the source outpaces the main thread, the queue grows unbounded. (The synthetic source produces 2 Hz, well below drain rate.)

