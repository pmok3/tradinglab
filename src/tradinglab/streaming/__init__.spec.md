# streaming/__init__.py — Spec

## Purpose
Aggregates streaming-source plugins (currently only `synthetic-stream`) into the `STREAM_SOURCES` registry. Registration mirrors `data/__init__.py`.

## Public API
- `STREAM_SOURCES: Dict[str, StreamSource]` — registry.
- `StreamSource`, `StreamCallback`, `EventKind` — protocol and aliases.
- `register_stream(name, source)` — imperative registration.
- `SyntheticStreamSource` — the built-in deterministic offline stream.
- `SchwabStreamSource` — the (broker) Schwab streaming adapter, also registered as `"schwab-stream"`. Activated only when Schwab credentials are configured; falls back to inert behaviour otherwise.

## Dependencies
- Internal: `.base`, `.synthetic`.
- External: none at init time.

## Design Decisions
- **Only registered source is synthetic** — `STREAM_SOURCES` currently contains `synthetic-stream` only. This is a deterministic offline simulator, NOT a real-time market tick feed. Do not use streaming output for live trading or any production decision.
- Separate registry from `DATA_SOURCES`: historical and streaming providers are different capabilities. A provider could register as both, or one or the other.
- `"synthetic-stream"` registers here and also in `DATA_SOURCES` (as a history bootstrap), so the app finds them both paths from the same name.

## Invariants
- `"synthetic-stream" in STREAM_SOURCES` after package import.

## Testing
- `check_90_streaming_dispatch` / `check_90b_stream_refresh` / `check_95_stream_queue_coalescing` exercise the full subscribe→tick→rollover path.

## Known limitations
- No live-data streaming source yet (would need a broker WebSocket).

