# positions/__init__.py — Spec

## Purpose
Public re-export shim for the `positions` package — single source of truth for paper / sandbox open positions during a session. Owned by the Tk main thread; the per-file design (mutable `Position`, frozen `PositionEvent` ledger, Tk-thread `PositionTracker`, atomic JSON persistence) is described in the sibling `spec.md` (package design notes).

## Public API (re-exports)
From `.model`: `Position`, `PositionEvent`, `PositionEventKind`, `PositionSide`.
From `.tracker`: `PositionTracker`, `Subscriber`.

Not re-exported (callers import directly when needed):
- `.model.PositionSource` — `Literal["sandbox", "manual"]`; only the tracker mutators need the type annotation.
- `.storage` — `save_open_positions` / `load_open_positions` / `save_trail_state` / `load_trail_state` / `clear_trail_state`. Persistence is opt-in; the tracker itself is in-memory.

## Dependencies
- Internal: `.model`, `.tracker`.
- External: stdlib only at this layer.

## Design Decisions
- **Narrow re-export surface**: only the types every consumer needs (events + tracker). Storage helpers are import-on-demand so smoke tests that don't need disk paths don't pay the import.
- **`PositionSource` intentionally not re-exported**: it's a `Literal`, not a class — consumers use the string values (`"sandbox"` / `"manual"`) directly.

## Invariants
- `import tradinglab.positions` succeeds in a headless environment (no Tk runtime needed for import; the runtime guard fires only on mutator calls).
- The six re-exported names always resolve.

## Testing
- Covered indirectly via sandbox smoke tests (`test_smoke_sandbox.py`) and the manual-paper-positions exit-tab smoke checks.

