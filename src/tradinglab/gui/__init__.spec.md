# gui/__init__.py — Spec

## Purpose
Marker for the `gui` subpackage. Holds widgets and event subsystems that would otherwise bloat `app.py`: dialogs, the interaction mixin (pan/zoom/hover/crosshair/click-to-type), the watchlist-tab mixin, the worker-pool mixin.

## Public API
None re-exported. Consumers import submodules (`dialogs`, `interaction`, `watchlist_tab`, `workers`) directly.

## Dependencies
- External: none at init time.

## Design Decisions
- **No import of `tradinglab.app` at module load** anywhere under `gui/`. Use `TYPE_CHECKING` for annotations. Prevents circular imports — `app.py` imports `gui.*`, and any reverse edge would deadlock at package init.
- Mixin rules (shared by all `gui/*Mixin` classes): no `__init__`, no cooperative `super()`, no name collisions with each other or with `ChartApp`. See the mixin files' own specs for why.

## Invariants
- No back-import of `tradinglab.app` at module scope.
- All mixins are pure behavior — state is initialized in `ChartApp.__init__`.
- **Tk-main-thread-only** — all Tk widget construction and mutation in `gui/*` occurs on the Tk thread. Cross-thread access via `self.after` queueing — but see `gui/watchlist_tab.spec.md` for the worker-inbox pattern that supersedes `after` for worker results.

## Testing
- `check_00_import` catches any accidental circular import.

## Known limitations / Future work
None.

