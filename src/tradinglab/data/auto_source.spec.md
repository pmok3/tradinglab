# data/auto_source.py — Spec

## Purpose
The **"Auto"** data source — the startup default — which resolves to the
**globally best available source** per the tier-aware priority in
`data/source_ranking.py` (`alpaca@paid > schwab > polygon >
yfinance+alpaca > yfinance > alpaca@free`).
Selecting Auto means "use the best real source I have configured"; the user can
still override to any concrete source in the dropdown.

Implemented as a first-class **delegating source** (same pattern as the hybrid):
the whole app keys off `source_var == "Auto"` (cache keys, drilldown, prefetch,
persistence) and `fetch_auto_data` resolves + delegates to the concrete best
source at fetch time. Extensible — adding Schwab later needs no change here; it
slots into the ranking and Auto starts choosing it once registered.

## Public API
- `AUTO_SOURCE_NAME = "Auto"` — the registry key + dropdown label.
- `resolve_auto_source(*, candidates=None) -> str` — the concrete source Auto
  currently resolves to: `source_ranking.best_source` over the user-visible
  candidates **excluding "Auto" itself** (never recurses) and internal sources
  (already filtered by `user_visible_sources`). Falls back to `"yfinance"` when
  no real source is available. `candidates` defaults to `user_visible_sources()`.
- `fetch_auto_data(ticker, interval, **_ignored) -> list[Candle] | None` — the
  `DataFetcher`. Resolves the best source and dispatches through the **live**
  `DATA_SOURCES` registry (so a test stub or a freshly-registered vendor is
  honoured). Extra range kwargs are ignored (Auto is registered period-style).
  Guards against dispatching to itself. Never raises (delegate errors → `None`).
  Records the delegate via `note_resolved_source` **before** dispatch.
- `last_resolved_source() -> str | None` — the concrete source Auto most
  recently delegated to, i.e. the provenance of whatever is cached under the
  opaque `"Auto"` cache key. `None` when nothing has resolved yet.
- `note_resolved_source(name)` — record that provenance (empty → `None`).

## Contract
- **Auto is always live-capable by construction.** Free/IEX Alpaca ranks below
  yfinance and yfinance is always registered, so Auto never resolves to
  free-Alpaca-as-a-live-source — it resolves to paid-Alpaca (SIP), a
  full-volume deep vendor such as Polygon / future Schwab, yfinance, or the
  `yfinance+alpaca` composite, all real-time on their live edge.
  `gui/polling._live_updates_delayed_for_source` still resolves `"Auto"` → its
  effective source before the delayed-feed check, so it stays correct if the
  priority ever changes.
- **Registered right after yfinance** (`data/__init__.py`), so
  `user_visible_sources()[0]` stays `"yfinance"` (many invariants + tests depend
  on that); Auto is the second dropdown entry. It is the **startup default** via
  `constants.BUILTIN_STARTUP_DEFAULTS["source"] = "Auto"`, NOT via first-visible.
- **Test / power-user seam:** `TRADINGLAB_STARTUP_SOURCE` (read by
  `AppState._resolve_source`) forces the active startup source ahead of the
  "Auto" default. `tests/conftest.py` sets it to `"yfinance"` so a real ChartApp
  boot stays deterministic + network-free (an Auto→hybrid boot would otherwise
  fetch real Alpaca).
- **Resolution is tracked because the cache key doesn't record it.** The
  `"Auto"` namespace is provider-agnostic, so nothing downstream can tell which
  vendor produced the bars on screen. `data/__init__` seeds
  `note_resolved_source(resolve_auto_source())` right after the boot-time
  `register_vendor_sources()` (a session that renders entirely from cache never
  calls `fetch_auto_data`, so the seed is the only baseline it gets), and every
  fetch rewrites it. `gui/source_registry_app` compares that against a fresh
  resolve to detect a mid-session credential save moving Auto's answer, and
  reloads instead of leaving the upgrade until the next app restart.

## Design Decisions
- **Delegating-source (not an app-level resolve-to-concrete mode):** reuses the
  registry pattern, so no changes to the ~20 `source_var.get()` call sites, and
  the merged/selected series gets a clean `"Auto"` cache namespace. The
  effective source is opaque in the cache key but resolved fresh on every fetch.
- **Dynamic `DATA_SOURCES` lookup** (not a captured fetcher ref) so smoke's
  `DATA_SOURCES[...] = stub` swaps AND a future vendor registration are picked up.
- **No partial-volume warning for "Auto"** (`quality.volume_quality("Auto")` →
  UNKNOWN): Auto never resolves to a partial-volume *live* source (its resolved
  targets are full-volume on the visible/recent window), so no false warning.

## Invariants
- `resolve_auto_source` never returns `"Auto"`; `fetch_auto_data` never
  dispatches to itself (self-dispatch guard → yfinance fallback).
- `fetch_auto_data` always records its delegate, including when the delegate
  is unregistered, raises, or is the self-dispatch fallback.
- Registered unconditionally, so `"Auto"` is always a valid `source_var` value
  and startup default.

## Testing
`tests/unit/data/test_auto_source.py` — resolve excludes-self/fallback,
yfinance-only, hybrid-over-yfinance, tier flip (paid→alpaca / free→yfinance via
monkeypatched `is_live_capable`); fetch delegate/none/error-swallow/self-guard;
`last_resolved_source` recording on each of those paths; registration order
(yfinance first-visible, Auto visible), BUILTIN default is Auto, and the
`TRADINGLAB_STARTUP_SOURCE` env-pin precedence in `AppState._resolve_source`.
`tests/unit/gui/test_source_registry_app.py` — the mid-session reload that
consumes this provenance.
