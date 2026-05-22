# watchlists/__init__.py — Spec

## Purpose
Aggregates the watchlist data layer — manager + storage + dataclass + import/export helpers. Pure data; no UI wiring.

## Public API
- `Watchlist` — dataclass `(name: str, tickers: List[str])`.
- `WatchlistManager` — explicit-save CRUD manager (mutations stay in memory until `save()` is called).
- `load_all`, `save_all` — low-level storage accessors.
- `export_to_file`, `import_from_file` — round-trippable JSON I/O.

## Dependencies
- Internal: `.manager`, `.storage`.
- External: none at init time.

## Design Decisions
- Data layer only; the Watchlists dialog (`gui/dialogs._WatchlistDialog`) and the Watchlist notebook tab (`gui/watchlist_tab.WatchlistTabMixin`) own their UI wiring.

## Invariants
- All public re-exports here are side-effect-free at import time (no filesystem touch).

## Testing
- Exercised by `check_d0_dialogs` (watchlist dialog) and `check_c0_watchlist_tab` (tab repaint).

## Known limitations / Future work
None.

