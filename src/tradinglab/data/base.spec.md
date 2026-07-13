# data/base.py — Spec

## Purpose
Defines the `DataFetcher` protocol (a `(ticker, interval) -> Optional[List[Candle]]` callable, optionally range-capable) and the `DATA_SOURCES` registry. Provides `register_source` as the plug-in hook, `user_visible_sources` for UI surfaces that must hide internal-only sources, and `fetch_range` for targeted range fetching.

## Public API
- `DataFetcher = Callable[..., Optional[List[Candle]]]` — type alias. Called as `fetcher(ticker, interval)` for the default trailing window; **range-capable** sources also accept kw-only `start` / `end` (aware datetimes) to fetch an explicit window. Returning `None` (or an empty list) signals failure (import error, network error, empty result — all treated equivalently by the app).
- `DATA_SOURCES: Dict[str, DataFetcher] = {}` — the global registry; insertion order preserved.
- `register_source(name, fetcher, *, internal=False, supports_range=False)` — idempotent; repeat calls overwrite. Intentional so smoke tests can stub real sources (e.g. `register_source("yfinance", fake)`). The fetcher is wrapped by `_ratio_aware` so it transparently resolves ratio pseudo-symbols (`NUM/DEN`) leg-by-leg through the SAME source — so ratios work on EVERY source, not just yfinance (see `ratio_source.spec.md`). `DATA_SOURCES[name]` is that wrapper; the raw fetcher is reachable via `DATA_SOURCES[name].__wrapped__`. `internal=True` dispatches programmatically while hiding the source from every user-facing UI surface; `supports_range=True` marks the fetcher as accepting kw-only `start`/`end` (targeted range fetch). A plain re-register clears both flags.
- `is_internal_source(name) -> bool` — predicate; True if the named source was registered with `internal=True`.
- `source_supports_range(name) -> bool` — True if the source was registered with `supports_range=True`.
- `fetch_range(source, ticker, interval, start_ts, end_ts) -> (Optional[List[Candle]], status)` — targeted fetch of `[start_ts, end_ts)` (epoch seconds, passed to the fetcher as aware-UTC datetimes). `status ∈ "ok" | "empty" | "unsupported" | "error"`. Returns `"unsupported"` (never raises) when the source can't range-fetch, so the caller falls back to the trailing window. See `docs/TARGETED_FETCH.md`.
- `user_visible_sources() -> list[str]` — returns `DATA_SOURCES` keys with internal-flagged entries filtered out, preserving original insertion order. Used by `app.py` (toolbar + ConfigManager), `gui/dialogs.py` (Settings → Startup parameters source dropdown), and `app._refresh_data_source_combobox` (post-BYOD-registration refresh).

## Dependencies
- Internal: `..models.Candle` (type).
- External: `typing`.

## Design Decisions
- **Plain dict, not a class**: the registry is a two-operation surface (add, read). A class would add ceremony without benefit.
- **Insertion order is public API** (via `next(iter(user_visible_sources()))`): the UI picks the first user-visible registered source as the default. This means `data/__init__.py`'s registration order is not optional — yfinance must be first.
- **Idempotent registration**: lets tests monkey-patch. Repeat registrations overwrite the existing entry without error. A re-registration without `internal=True` un-flags the entry (so a test that calls `register_source("synthetic", fake)` un-hides synthetic from the UI). In practice tests stub via `DATA_SOURCES[...] = ...` direct assignment which bypasses the helper, so the flag survives.
- **`internal` is a parallel set, not a dict-value attribute** (`_INTERNAL_SOURCES: set[str]`): keeps `DATA_SOURCES`'s value type homogeneous (`DataFetcher`) so callers that walk `.values()` don't have to special-case anything. The set is private; check membership via `is_internal_source(name)` or filter via `user_visible_sources()`.
- **`supports_range` is a parallel set** (`_RANGE_CAPABLE: set[str]`, like `_INTERNAL_SOURCES`) — keeps `DATA_SOURCES`'s value type homogeneous. `fetch_range` returns a `(candles, status)` tuple rather than raising, so the caller (drilldown) can distinguish *unsupported* (fall back to trailing window) from *empty* (provider has no bars in range) from *error*. Only Alpaca is `supports_range=True` today; Polygon/yfinance stay trailing-window until a follow-up.
- **Ratio-awareness is installed at registration** (`_ratio_aware`): every fetcher is wrapped so a typed `NUM/DEN` ratio symbol is decomposed into its two legs and each is fetched from the SAME source, then combined by `ratio_source.compute_ratio_candles`. This makes ratio resolution **source-agnostic** — previously only `yfinance_source.fetch_live_data` had an internal hook, so a ratio typed while Alpaca/Polygon was the active source was passed verbatim to a vendor that has no symbol named `IGV/SMH` and failed with "check that both legs are valid tickers". Wrapping at the single registration chokepoint fixes it at all ~20 `DATA_SOURCES.get(...)` dispatch sites at once (app, fetch_service, sandbox, chartstack, runner, drilldown, polling, watchlist) without per-site edits. The wrapper forwards `**kwargs` (range-fetch `start`/`end`) to each leg, is idempotent (marker attr `_tl_ratio_aware`, so re-registering a wrapped fetcher never double-wraps), and preserves the raw fetcher as `__wrapped__` (`functools.wraps`). yfinance keeps its own internal hook too (now redundant for the registry path but retained for any direct `fetch_live_data` importer).

## Invariants
- After `register_source(n, f)`, `DATA_SOURCES[n]` is a **ratio-aware wrapper** delegating to `f` (see Design Decisions); the raw fetcher is `DATA_SOURCES[n].__wrapped__ is f`. Re-registering an already-wrapped fetcher does not double-wrap (`_tl_ratio_aware` guard).
- After `register_source(n, f, internal=True)`, `is_internal_source(n) is True` and `n not in user_visible_sources()` but `n in DATA_SOURCES`.
- `user_visible_sources()` preserves the registration order of the underlying dict (so the first user-visible entry remains the default selection).
- **Sources MUST NOT propagate raw network errors to callers.** Exceptions are caught at the source layer; failures return `None`/`[]` and log via `_status` or `print`. See `data/yfinance_source.py:45` for the canonical implementation. BYOD local sources (see `data/local_source.spec.md`) obey the same no-exception contract — schema and I/O failures return `None` after a status-bar log.
- Asset-class scope of the currently registered sources is documented in `data/__init__.spec.md` (US equities / ETFs only).

## Testing
- Exercised by `check_70_fetch_executor` and any smoke check that runs a real fetch.
- `tests/unit/data/test_user_visible_sources.py` pins the internal-flag contract: synthetic / synthetic-stream are in `DATA_SOURCES` but excluded from `user_visible_sources()`; yfinance is the first user-visible entry.
- `tests/unit/data/test_ratio_source.py` pins source-agnostic ratio resolution at registration: a vendor-style source that returns `None` for a `/` symbol still serves ratios via the wrapper (the source never sees `NUM/DEN`), idempotent no-double-wrap, and range-kwarg forwarding to both legs.

## Known limitations
- No "unregister"— tests that stub must remember to restore, or rely on the fact that the next `data/__init__.py` import re-registers defaults (though Python only imports once per session).

