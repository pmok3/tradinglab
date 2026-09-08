# streaming/base.py — Spec

Last updated: 2026-09-07

## Purpose
Declares the `StreamSource` protocol, optional lifecycle value types, and the
`STREAM_SOURCES` registry. A stream source emits **tick** (in-progress bar
changed), **rollover** (new bar), or optionally **closed** (authoritative
timestamp upsert) events for a `(ticker, interval)` subscription.

## Public API
- `EventKind = str` (alias; `"tick"`, `"rollover"`, optional `"closed"`).
- `StreamCallback = Callable[[EventKind, Candle], None]`.
- `class StreamSource(Protocol)`: `subscribe(ticker, interval, on_event) -> unsubscribe: Callable[[], None]`.
- `STREAM_SOURCES: Dict[str, StreamSource]`.
- `register_stream(name, source)`.
- `StreamState(str, Enum)`: `IDLE`, `CONNECTING`, `LIVE`, `STALE`,
  `DISCONNECTED`, `AUTH_REQUIRED`, `ERROR`, `CLOSED`.
- `StreamStatus`: frozen dataclass with `state`, sanitized `message`,
  `changed_at`, `last_received_at=None` (both monotonic seconds), and
  `generation=0` (connection epoch, not symbol-set revision).

## Dependencies
- Internal: `..models.Candle` (type).
- External: stdlib `typing`, `enum`, `dataclasses`.

## Design Decisions
- **Subscription returns an unsubscribe callable**, not a subscription object. Minimal API surface, easy to stash in a list and clean up.
- **Real vs simulated must be self-documented** — Implementations MUST document in their own spec whether they emit real market ticks or simulated ones. The UI surfaces stream source by name; check the source's `__doc__` and spec before relying on the data.
- **`STREAM_SOURCES` registration is idempotent** — The registry is normally populated when the streaming package is imported, and tests may replace entries by re-registering a name.
- **Contract: at most one trailing event may still complete after unsubscribe** (a caller-visible race with the source's own thread). Consumers must be idempotent to a single trailing callback. The app's token-gating path (`_stream_token` stamped into every event) makes this explicit.
- **Callbacks may fire from any thread** — the consumer is responsible for marshalling. Callback implementations MUST be thread-safe or post to a thread-safe queue. `ChartApp._start_stream_if_applicable` installs a closure callback that enqueues events into `self._stream_queue` (a thread-safe `queue.Queue`); `ChartApp._drain_stream_queue` pulls them on the Tk main thread via `after()`.
- **Authoritative provenance is explicit.** Schwab `CHART_EQUITY` emits
  `closed`, an upsert by bar timestamp, including older corrections.
  Provisional LEVELONE and exact CHART bars cannot be distinguished by
  their timestamps or `tick` alone. Existing synthetic `tick`/`rollover`
  behavior is unchanged; consumers using Schwab must handle `closed`.
- **Lifecycle is an optional capability.** `StreamSource` still requires
  only `subscribe`; legacy sources need not implement health or close.
  Schwab exposes `get_status(ticker=None)` and terminal idempotent
  `close()`. Its spec defines ACK, stale, symbol-readiness and epoch rules.

## Invariants
- After `unsubscribe()` returns, at most one more callback may fire (never more).
- `STREAM_SOURCES` is idempotent on re-registration.

## Testing
- `check_90_streaming_dispatch`: subscribe → event dispatch → unsubscribe.
- `check_95_stream_queue_coalescing`: several rapid ticks coalesce into one redraw.
- `tests/streaming/test_schwab_connection.py`: immutable status, epoch
  changes, health/ACK gating, explicit authoritative `closed` events.

## Known limitations
- No built-in backpressure. If the source outpaces the main thread, the queue grows unbounded. (The synthetic source produces 2 Hz, well below drain rate.)
