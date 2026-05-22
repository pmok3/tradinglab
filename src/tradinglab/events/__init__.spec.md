# events/__init__.py — Spec

## Purpose
Package entry point for the **Earnings & Dividends** ambient-context feature. Mirrors `tradinglab.data` in shape: provider-pluggable fetchers register against a global `EVENT_SOURCES` dict; consumers (`SandboxController`, `events.render`) iterate / resolve by name.

## Public API
Re-exports from `.base`:
- `EarningsRecord`, `DividendRecord`, `EventBundle` — canonical record types.
- `EventFetcher` — the protocol type.
- `EVENT_SOURCES` — the registry dict.
- `register_event_source(name, fetcher)`.

The `events_source` tunable (see `tradinglab.defaults`) selects the default provider; consumers fall back to `"synthetic"` when the named source is unavailable or the fetch fails.

## Dependencies
Internal: `.base`, conditionally `.synthetic_events`, `.yfinance_events`. External: none.

## Design Decisions
- **Conditional registration.** `yfinance_events` registers only when `yfinance` imports cleanly; `synthetic_events` always registers. Mirrors `data/__init__.py` and lets headless smokes run without network.
- **Single registry per source name.** Re-registration overwrites — smoke harness injects stubs this way.
- **No matplotlib / Tk at package level.** Render glyphs live in `.render` and are imported lazily from the GUI; the events package stays headless.

## Invariants
- `EVENT_SOURCES` is non-empty after package import (synthetic always registers).
- The first source registered (`"yfinance"` when available, else `"synthetic"`) is the default for `defaults.get("events_source")`.

## Algorithm
1. Package import runs registrations as side-effects.
2. Caller resolves a fetcher: `fetcher = EVENT_SOURCES[source_name]`.
3. Fetcher returns an `EventBundle` or `None`.
4. Caller stores via `SandboxController.set_event_bundle`, which feeds dividends back to the engine as `CorporateAction`s.
