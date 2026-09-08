# streaming/registry.py — Spec

Last updated: 2026-09-07

## Purpose
Resolve main-chart streaming capabilities separately from historical provider
names and reconcile credential-gated bar/quote registration without network I/O.

## Public API
- `ChartStreamCapability(stream_name, native_intervals, adapted_intervals, equities_only)`.
- `CHART_STREAM_CAPABILITIES`: explicit provider bindings.
- `resolve_chart_stream(source_name, ticker, interval, stream_sources=...)`
  returns `ChartStreamSelection(source, provider, native_interval)` or `None`.
- `reconcile_vendor_streams(reset=False, oauth_connected=None) -> bool`: presence-based registration,
  identity-preserving refresh, or terminal reset of the shared Schwab instance.
- `close_vendor_streams() -> bool`: terminally close/unregister both axes
  before credential identity invalidation.
- `CloseableStream`: optional structural lifecycle capability.
- `quote_registry_binding(name)`: identity tuple of factory and its shared bar
  transport, allowing existing windows to distinguish a harmless registry
  refresh from a terminal source replacement without vendor branching in UI.

## Dependencies
- Internal: bar/quote registries, Schwab source/factory, intraday interval policy,
  credential resolver, Auto source/provenance and symbol classifiers.
- External: stdlib only.

## Design Decisions
- **Explicit capabilities.** `schwab` maps to `schwab-stream`; native 1m and the
  four chart-adapted intervals are declared rather than inferred from suffixes.
  Unmapped exact-name plugins retain their legacy contract.
- **Provider provenance.** Auto uses the existing tier-aware resolver and must
  agree with recorded cache provenance. Event cache keys still use the chart's
  chosen historical name.
- **Shared singleton.** Unchanged refresh preserves bar instance and quote
  factory. Reset/removal closes the old instance and unregisters both axes.
  A terminally closed source is never reused.
- **No commissioning bypass.** Presence registration never probes credentials,
  refreshes a token, performs REST, or registers a historical source.
- **Token ownership.** Credential callers invalidate tokens on identity change.
  OAuth reset only retires the transport, never the newly saved token.
- **Explicit disconnect.** `oauth_connected=False` suppresses in-process
  registration until OAuth reconnect or an effective identity change. Ordinary
  credential refresh cannot resurrect a deliberately disconnected source.
  Restart still performs only presence-based registration.

## Invariants
- Synthetic and unrelated plugins survive vendor reconciliation.
- Schwab scaled/quotient/index symbols remain unsupported; COMP/MOVE remain
  equities under the shared curated alias rules.
- Without credentials neither Schwab registry entry remains.
- A harmless save does not terminate quote subscribers.

## Testing
`tests/streaming/test_registry.py`: mapping, interval/symbol guards, Auto
provenance, no-key lifecycle and shared source/factory identity.

## Known limitations / Future work
REST registration stays disabled pending live commissioning. This does not
expose a new user-visible streaming source picker.

## Recent history
- 2026-09-07: dynamic keyless chart capability integration.
