# quant/__init__.py — Spec

## Purpose
Package init for the market-internals ("Quant") domain. Re-exports the
catalog surface so callers can `from tradinglab.quant import
available_symbols` without reaching into the module.

## Public API
Re-exports from `catalog.py`: `QUANT_CATALOG`, `UNAVAILABLE_SYMBOL_TEXT`,
`QuantGroup`, `QuantRow`, `available_rows`, `available_symbols`,
`iter_rows`, `row_for_key`.

## Dependencies
- Internal: `catalog.py`.
- External: none.

## Design Decisions
- **Re-export only.** No logic lives here, so importing the package is cheap
  and free of Tk / data-layer side effects.

## Invariants
- `__all__` matches `catalog.__all__`.

## Testing
Covered indirectly by `tests/unit/quant/test_catalog.py`.

## Out of scope
Fetching, rendering, and persistence. See `catalog.spec.md` and
`../gui/quant_tab.spec.md`.

## Recent history
- Initial version.
