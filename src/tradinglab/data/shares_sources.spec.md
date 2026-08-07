# data/shares_sources.py — Spec

## Purpose
Registry of historical shares-outstanding providers, and the resolution
of the `shares_data_source` tunable to a concrete fetcher. Mirrors the
price-source registry in [`data/base`](base.spec.md): providers register
under a name, consumers resolve a name. The point of the indirection is
that **no module hardcodes a vendor** — the active provider is a setting,
resolved by a higher-level caller (the sandbox heatmap window) and
injected downstream, so swapping SEC EDGAR for a paid fundamentals feed
is a registration plus a settings change rather than a refactor.

## Public API
- `class SharesFact(NamedTuple)` — `as_of_ts: int`, `filed_ts: int`,
  `shares: float`. JSON round-trips as `[as_of_ts, filed_ts, shares]`.
- `SharesFetcher = Callable[[str], list[SharesFact]]` — ascending by
  `as_of_ts`.
- `DEFAULT_SHARES_SOURCE = "edgar"`.
- `SHARES_SOURCES: dict[str, Callable[..., SharesFetcher]]` — registered
  **factories**, not instances.
- `register_shares_source(name, factory)` — idempotent; repeat
  registrations overwrite so tests can stub a real provider.
- `unregister_shares_source(name) -> bool`.
- `available_shares_sources() -> list[str]` — sorted names.
- `null_shares_fetcher(symbol) -> []` — knows nothing, touches no
  network, never raises.
- `resolve_shares_fetcher(name=None, **kwargs) -> (resolved_name, fetcher)`
  — `name` defaults to the `shares_data_source` tunable, then to
  `DEFAULT_SHARES_SOURCE`. `kwargs` are forwarded to the factory
  (e.g. `cik_lookup`).

## Dependencies
- Internal: [`defaults`](../defaults.spec.md) (read lazily inside
  `resolve_shares_fetcher`, so importing this module never pulls in
  settings).
- External: stdlib only.

## Design Decisions
- **`SharesFact` carries two dates, and both are load-bearing.**
  `as_of_ts` is the date the count describes — the anchor for the
  split-basis lift, because the count is on *that* date's basis.
  `filed_ts` is when the number became public; a replay consumer must
  filter on it, because a count is not knowable before it is filed and
  the gap is typically two weeks. A provider that cannot supply
  `filed_ts` is not point-in-time correct and should not be registered
  without documenting the residual.
- **Factories, not instances.** A provider may hold per-session caches
  (EDGAR caches the SEC ticker map in-process); registering a factory
  keeps that out of module-level shared mutable state and lets each
  consumer pass its own `cik_lookup`.
- **An unknown name resolves to `null_shares_fetcher`, never to another
  vendor.** Silently substituting a different source would present
  numbers from a provider the user did not select as if they were the
  configured one. "Sizes unavailable" (tiles render approximate) is the
  honest failure. The same applies when a factory raises — a broken
  provider must not break session start.
- **`[]` is the contract for "no data"** (non-filer, outage, pre-XBRL);
  fetchers never raise into the render path.
- **Resolution happens at a higher level, not here.** This module
  provides the mechanism; the *policy* (which tunable, which
  `cik_lookup`) lives in the caller. `resolve_shares_fetcher` reads the
  tunable only as a convenience default.

## Invariants
- `resolve_shares_fetcher` never raises and always returns a callable.
- A fetcher returned by resolution never performs network I/O at
  resolution time — only when called for a symbol.
- Registered names are lower-cased and non-empty.

## Testing
- `tests/unit/data/test_edgar_shares.py` — registration round-trip,
  `edgar` registered by the `data` package, resolution defaults to
  `edgar`, an unknown name yields `null_shares_fetcher` rather than a
  substitute, a raising factory degrades instead of propagating, and
  kwargs reach the factory.

## Known limitations / Future work
- No per-provider capability metadata yet (depth, cadence, coverage).
  When a second provider lands, that belongs here alongside the
  registry — mirroring `data/quality.py` for price sources.

## Recent history
- Introduced so the shares source became a tunable rather than a
  hardcoded import, when EDGAR replaced the price vendor's fundamentals
  feed as the sole share-count source.
