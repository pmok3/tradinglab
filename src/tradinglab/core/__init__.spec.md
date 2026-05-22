# core/__init__.py — Spec

## Purpose
Marker for the `core` subpackage: pure-compute primitives (candle pair filtering/alignment, vectorized numpy series views, y-limit/viewport math). The invariant enforced by this module's docstring: **no module under `core/` imports Tkinter, matplotlib, or `tradinglab.app`**. This is the layer backtesters, replay engines, and headless strategy simulations can consume without the GUI.

## Public API
- None directly; module is a namespace marker with a docstring. Users import submodules (`pairing`, `series`, `viewport`).

## Dependencies
- Internal: none at package-init time.
- External: none.

## Design Decisions
- Keeping `core/` GUI-free decouples the math from matplotlib's import cost and makes the entire layer unit-testable in a sandbox without a display.

## Invariants
- No Tk / mpl / `app` imports anywhere in the subpackage.

## Testing
- Implicit: `check_00_import` validates the subpackage loads without pulling Tk.

## Known limitations / Future work
- If a replay engine is added, it should live alongside `core/` (not inside `gui/` or `app.py`).

