# data/base.py — Spec

## Purpose
Defines the `DataFetcher` protocol (a `(ticker, interval) -> Optional[List[Candle]]` callable) and the `DATA_SOURCES` registry. Provides `register_source` as the plug-in hook plus `user_visible_sources` for UI surfaces that must hide internal-only sources.

## Public API
- `DataFetcher = Callable[[str, str], Optional[List[Candle]]]` — type alias. Returning `None` (or an empty list) signals failure (import error, network error, empty result — all treated equivalently by the app).
- `DATA_SOURCES: Dict[str, DataFetcher] = {}` — the global registry; insertion order preserved.
- `register_source(name, fetcher, *, internal=False)` — idempotent; repeat calls overwrite. Intentional so smoke tests can stub real sources (e.g. `register_source("yfinance", fake)`). Pass `internal=True` to dispatch programmatically while hiding the source from every user-facing UI surface (combobox / dropdown).
- `is_internal_source(name) -> bool` — predicate; True if the named source was registered with `internal=True`.
- `user_visible_sources() -> list[str]` — returns `DATA_SOURCES` keys with internal-flagged entries filtered out, preserving original insertion order. Used by `app.py` (toolbar + ConfigManager), `gui/dialogs.py` (Settings → Startup parameters source dropdown), and `app._refresh_data_source_combobox` (post-BYOD-registration refresh).

## Dependencies
- Internal: `..models.Candle` (type).
- External: `typing`.

## Design Decisions
- **Plain dict, not a class**: the registry is a two-operation surface (add, read). A class would add ceremony without benefit.
- **Insertion order is public API** (via `next(iter(user_visible_sources()))`): the UI picks the first user-visible registered source as the default. This means `data/__init__.py`'s registration order is not optional — yfinance must be first.
- **Idempotent registration**: lets tests monkey-patch. Repeat registrations overwrite the existing entry without error. A re-registration without `internal=True` un-flags the entry (so a test that calls `register_source("synthetic", fake)` un-hides synthetic from the UI). In practice tests stub via `DATA_SOURCES[...] = ...` direct assignment which bypasses the helper, so the flag survives.
- **`internal` is a parallel set, not a dict-value attribute** (`_INTERNAL_SOURCES: set[str]`): keeps `DATA_SOURCES`'s value type homogeneous (`DataFetcher`) so callers that walk `.values()` don't have to special-case anything. The set is private; check membership via `is_internal_source(name)` or filter via `user_visible_sources()`.
- **Return `None` OR empty list for failure**: both mean "give up, move on to fallback". Callers check truthiness (`if not candles: ...`), so they don't need to branch on which shape was returned.

## Invariants
- After `register_source(n, f)`, `DATA_SOURCES[n] is f`.
- After `register_source(n, f, internal=True)`, `is_internal_source(n) is True` and `n not in user_visible_sources()` but `n in DATA_SOURCES`.
- `user_visible_sources()` preserves the registration order of the underlying dict (so the first user-visible entry remains the default selection).
- **Sources MUST NOT propagate raw network errors to callers.** Exceptions are caught at the source layer; failures return `None`/`[]` and log via `_status` or `print`. See `data/yfinance_source.py:45` for the canonical implementation. BYOD local sources (see `data/local_source.spec.md`) obey the same no-exception contract — schema and I/O failures return `None` after a status-bar log.
- Asset-class scope of the currently registered sources is documented in `data/__init__.spec.md` (US equities / ETFs only).

## Testing
- Exercised by `check_70_fetch_executor` and any smoke check that runs a real fetch.
- `tests/unit/data/test_user_visible_sources.py` pins the internal-flag contract: synthetic / synthetic-stream are in `DATA_SOURCES` but excluded from `user_visible_sources()`; yfinance is the first user-visible entry.

## Known limitations
- No "unregister"— tests that stub must remember to restore, or rely on the fact that the next `data/__init__.py` import re-registers defaults (though Python only imports once per session).

