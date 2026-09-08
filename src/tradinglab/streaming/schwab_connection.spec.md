# streaming/schwab_connection.py - Spec

Last updated: 2026-09-07

## Purpose
Focused socket worker for the shared Schwab source. Owns auth/preferences,
wire requests, service ACK state, heartbeats, reconnect and cleanup. It does
not own chart UI, REST history, bar aggregation or quote book state.

## API
- Internal `_Connection(source)`: `start()`, nonblocking `shutdown()`,
  `stopping`. One daemon worker, no clock thread.
- `build_login_request(info, token, request_id=0)` and
  `build_subs_request(service, symbols, fields, streamer_info, request_id,
  *, command="SUBS")` are pure helpers. Command selection is explicit,
  **never inferred from correlation ID**. `UNSUBS` omits fields.
- `fetch_streamer_info(token)`: bounded `/userPreference` request through
  `credentialed_opener`; validates required per-user string fields and a
  secure `wss://` socket URL. No response bodies or identifiers in errors.
- Field constants for LEVELONE (union of bar/quote requirements) and CHART
  (0-8); `_BACKOFF = (1, 2, 4, 8, 16, 30)`.

## Dependencies
- Source facade via type-only import, `streaming/base` lifecycle,
  `schwab_aggregator` decoders, `data._http` credential-aware opener.
- Auth calls `get_access_token(get_credentials().schwab)` lazily inside
  the worker, preserving the existing auth API.
- Optional `websocket-client` loaded only when creating a socket. Adapter
  converts its failures to sanitized stdlib I/O errors for the worker.

## Design Decisions
- Worker is the only writer of connection/protocol state and socket I/O.
  Source desired sets/image revisions are snapshotted under its lock;
  no network operation executes inside that lock or on Tk subscribe/close.
- A process-wide **Schwab ownership lock** serializes connection attempts
  across distinct source instances, including terminal-close/recreate on
  registry credential changes. This is a vendor lifetime permit, not a
  subscription-state lock: only workers acquire it, with interruptible
  0.25s timed acquisition. Waiting sources remain CONNECTING.
  Ownership spans auth/preferences, connect/serve, and socket `finally`
  cleanup; it is released before reconnect backoff. A cancelled waiter
  exits without auth or dialing, including cancellation just after acquire.
  Registry/UI callers can replace a source immediately without joins,
  polling for physical closure, or timing-dependent registration hacks.
- Login request ID 0; wait for its correlated ACK, tolerating intervening
  heartbeat frames. Initial **SUBS independently for each service**.
  LEVELONE wants bar+quote union; CHART wants bar symbols only.
- Subsequent updates are sorted/batched `ADD` and `UNSUBS` per service,
  never one message per symbol. A newly desired service uses SUBS even
  if its first request ID is large. Commands keep unique IDs.
- One pending request per service. Only ACK success updates acknowledged
  wire membership. Changes during pending ACKs reconcile afterwards.
  Source image revisions trigger ADD for new consumers of existing symbols.
  A returned/new quote subscriber gets a re-image for previous close.
- LIVE requires successful login, no pending requests, reconciled per-service
  sets/image revisions, and an unchanged source subscription generation.
  Data arriving before ACK may dispatch but cannot prematurely mark LIVE.
- Every known-service error response, unmatched ACK, nonzero service
  notification or explicit symbol error fails the connection. Login failure
  is AUTH_REQUIRED; service rejection is ERROR for the shared source (even
  when another service succeeded). Vendor error text is never logged.
- Heartbeats, matched ACKs and recognized symbol data refresh transport
  receive time. ACK deadline is 15s; no traffic for 30s is STALE/reconnect.
  A symbol's old print does not determine socket liveness.
- Reconnect gets a fresh token and preferences on every attempt. `None`
  from refresh means AUTH_REQUIRED, **never fallback to the prior token**.
  Builders, service wire state, IDs and images reset for every epoch.
- Backoff starts at 1s, caps at 30s and resets after a session reaches ACK
  readiness. Event waits are interruptible. Shutdown guards after each
  auth/preferences/connect step prevent continued login after cancellation.
- `finally` closes any created socket on all exits, including rejected
  login, partial subscription failure and malformed response. Receive timeout
  is 0.25s; connection/preferences calls use 15s timeouts. Only after cleanup
  does the worker notify its source, allowing a replacement worker.
- Broad catches exist only at subscriber/worker fault boundaries and log
  explicit sanitized failures; ordinary network/protocol errors are typed
  and handled specifically. No success-shaped send failures.

## Invariants
- One socket process-wide across Schwab source instances, all consumers
  of an instance multiplexed. An active owner must close before another
  source can serve; accidental overlapping instances wait rather than
  evicting the established vendor socket.
- The connection may not reconnect or deliver further batch entries after
  terminal close; last-symbol removal cancels the worker.
- No retained token fallback, raw-frame logging or subscription-state-lock-held I/O.
- Protocol state size is bounded by active symbols and at most two service
  requests; connection attempts discard old state.

## Testing
`tests/streaming/test_schwab_connection.py`: scripted WebSocket envelopes
drive the actual login/serve/reconcile path. Covers initial SUBS, incremental
union updates, bar/quote joining, re-image, pending changes, service rejection,
deadlines/heartbeat, reconnect/token reset, backoff, epoch/health gating,
callback close, cleanup and worker-only auth/shutdown races. No real keys,
credentials, sockets or HTTP.
Distinct-source churn tests hold old auth/preferences/connect or socket
cleanup at explicit barriers, prove replacements wait through cleanup,
and cancel queued replacements before releasing ownership.

## Known limitations
Live vendor behavior still needs verification: entitlements/delayed delivery,
duplicate ADD image semantics, practical symbol ceiling and reconnect
throughput. Fake sockets prove the local state machine, not vendor access.
