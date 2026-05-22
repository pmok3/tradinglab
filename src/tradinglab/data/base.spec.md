# data/base.py — Spec

## Purpose
Defines the `DataFetcher` protocol (a `(ticker, interval) -> Optional[List[Candle]]` callable) and the `DATA_SOURCES` registry. Provides `register_source` as the plug-in hook.

## Public API
- `DataFetcher = Callable[[str, str], Optional[List[Candle]]]` — type alias. Returning `None` (or an empty list) signals failure (import error, network error, empty result — all treated equivalently by the app).
- `DATA_SOURCES: Dict[str, DataFetcher] = {}` — the global registry; insertion order preserved.
- `register_source(name, fetcher)` — idempotent; repeat calls overwrite. Intentional so smoke tests can stub real sources (e.g. `register_source("yfinance", fake)`).

## Dependencies
- Internal: `..models.Candle` (type).
- External: `typing`.

## Design Decisions
- **Plain dict, not a class**: the registry is a two-operation surface (add, read). A class would add ceremony without benefit.
- **Insertion order is public API** (via `next(iter(DATA_SOURCES))`): the UI picks the first registered source as the default. This means `data/__init__.py`'s registration order is not optional — yfinance must be first.
- **Idempotent registration**: lets tests monkey-patch. Repeat registrations overwrite the existing entry without error.
- **Return `None` OR empty list for failure**: both mean "give up, move on to fallback". Callers check truthiness (`if not candles: ...`), so they don't need to branch on which shape was returned.

## Invariants
- After `register_source(n, f)`, `DATA_SOURCES[n] is f`.
- **Sources MUST NOT propagate raw network errors to callers.** Exceptions are caught at the source layer; failures return `None`/`[]` and log via `_status` or `print`. See `data/yfinance_source.py:45` for the canonical implementation. BYOD local sources (see `data/local_source.spec.md`) obey the same no-exception contract — schema and I/O failures return `None` after a status-bar log.
- Asset-class scope of the currently registered sources is documented in `data/__init__.spec.md` (US equities / ETFs only).

## Testing
- Exercised by `check_70_fetch_executor` and any smoke check that runs a real fetch.

## Known limitations
- No "unregister"— tests that stub must remember to restore, or rely on the fact that the next `data/__init__.py` import re-registers defaults (though Python only imports once per session).

