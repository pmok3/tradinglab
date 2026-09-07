# streaming/__init__.py — Spec

## Purpose
Aggregates bar and quote streaming-source plugins into the `STREAM_SOURCES` and
`QUOTE_SOURCES` registries. Registration mirrors `data/__init__.py`.

## Public API
- `STREAM_SOURCES: Dict[str, StreamSource]` — registry.
- `StreamSource`, `StreamCallback`, `EventKind` — protocol and aliases.
- `register_stream(name, source)` — imperative registration.
- `SyntheticStreamSource` — the built-in deterministic offline bar stream.
- `SchwabStreamSource` — the Schwab bar-stream adapter, registered as
  `"schwab-stream"` when Schwab credentials are configured.
- `SyntheticQuoteSource` — the built-in deterministic offline quote source.
- `QUOTE_SOURCES`, `register_quote_source`, and quote protocol types — the
  many-symbol quote-stream registry and API.
- Schwab quotes are registered as `"schwab-quotes"` alongside the bar source
  when credentials are configured; both share one lazily-created Schwab
  streamer connection.

## Dependencies
- Internal: `.base`, `.synthetic`, `.quotes`, `.quote_book`, `.synthetic_quotes`,
  `.schwab`, `.schwab_quotes`, and `data.credentials`.
- External: none at init time.

## Design Decisions
- The synthetic sources are always registered for deterministic offline use.
  Schwab bar and quote sources are registered only when Schwab credentials are
  present; registration performs no network I/O and the connection opens on
  first subscription.
- Separate registry from `DATA_SOURCES`: historical and streaming providers are different capabilities. A provider could register as both, or one or the other.
- `"synthetic-stream"` registers here and also in `DATA_SOURCES` (as a history bootstrap), so the app finds them both paths from the same name.

## Invariants
- `"synthetic-stream" in STREAM_SOURCES` and `"synthetic-quotes" in
  QUOTE_SOURCES` after package import.
- With configured Schwab credentials, `"schwab-stream"` is in
  `STREAM_SOURCES` and `"schwab-quotes"` is in `QUOTE_SOURCES`; without them,
  neither source is registered.

## Testing
- `check_90_streaming_dispatch` / `check_90b_stream_refresh` / `check_95_stream_queue_coalescing` exercise the full subscribe→tick→rollover path.

## Known limitations
- The Schwab adapter has not been exercised against a live feed; see the
  Schwab-specific spec for known limitations.
