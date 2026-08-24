# quant/catalog.py — Spec

## Purpose
Defines the curated set of market-internals rows rendered by the **Quant**
side tab (`gui/quant_tab.py`). The Quant tab is a launcher: each row names a
quantity that describes the state of the *market* rather than one company,
and double-clicking it loads that quantity onto the chart. This module holds
only data — symbol, display name, one-line meaning — so the catalog is the
single source of truth for "what the quant set is", including for sandbox
preload and cache-export views.

## Public API
- `QuantRow(key, name, symbol, description, available=True,
  unavailable_reason="")` — one row. Frozen dataclass.
- `QuantGroup(key, name, rows=())` — one collapsible section.
- `QUANT_CATALOG: tuple[QuantGroup, ...]` — the shipped catalog, in display
  order.
- `UNAVAILABLE_SYMBOL_TEXT` — em-dash placeholder shown in the Symbol column
  of a row with no feed.
- `iter_rows(catalog=None)` — flatten groups into a row iterator.
- `available_rows(catalog=None)` — rows that name a fetchable symbol.
- `available_symbols(catalog=None)` — each fetchable symbol once, in catalog
  order; de-duplicated case-insensitively.
- `quant_leg_symbols(catalog=None)` — each vendor-fetchable leg behind the
  catalog once, in catalog order; de-duplicated case-insensitively.
- `row_for_key(key)` — lookup by stable key, else `None`.

## Dependencies
- Internal: `data/ratio_source` is imported lazily inside
  `quant_leg_symbols()`. The catalog stays import-free at module scope so it
  can be read by the GUI, tests, and preload/export seams without dragging in
  Tk or the data layer.
- External: `dataclasses`, `typing`.

## Design Decisions
- **Data, not behaviour.** No fetching, formatting, or Tk lives here. The tab
  owns rendering; `gui/quant_app.py` owns fetching. Keeping the catalog inert
  is what lets the sandbox / export prefetch consume it later without a GUI
  import.
- **Index shorthand, not vendor forms.** Rows use `VIX` / `VXN` / `TNX`
  rather than `^VIX`, so `data/index_aliases.py` resolves each to the active
  source's vocabulary (AGENTS.md §7.37). A row is written once and works on
  every vendor.
- **`^MOVE` is written with its caret, on purpose.** Bare `MOVE` is a real
  listed equity and is in `index_aliases.NEVER_ALIAS`; only the explicit
  `^MOVE` form resolves to the bond-volatility index. Writing the shorthand
  here would chart the equity. See `../data/index_aliases.spec.md`.
- **Expected-move divisors are √periods-per-year.** Implied volatility is
  quoted annualised, so `VIX/15.87` (√252), `VIX/7.21` (√52) and `VIX/3.46`
  (√12) convert it to a one-sigma move over one day, week, and month. These
  are *scaled symbols*, not quotients: dividing by a positive constant is
  order-preserving, so highs stay highs and no bar is dropped (§7.37).
- **Unavailable rows still ship.** `GEX` and `DIX` carry no symbol and render
  disabled. Listing them makes the gap visible; omitting them would make a
  market-internals panel quietly incomplete. The only free public feed is
  SqueezeMetrics' daily CSV, which is daily-only with a single value per day
  (no OHLC, no intraday) — wiring it is deliberately deferred.
- **Ratios are chosen for what they say, not for novelty.** Each quotient row
  is a relative-strength read whose direction has an agreed meaning:
  `RSP/SPY` for breadth, `HYG/LQD` for credit stress, `XLY/XLP` for equity
  risk appetite.
- **Rows and legs are different seams.** `available_symbols()` returns the
  row symbols the user sees, including ratios. `quant_leg_symbols()` returns
  the fetchable instruments behind those rows: plain rows map to themselves,
  quotient ratios add both legs, and scaled symbols add only the numerator.
  Ratios are never persisted by `disk_cache`, so preload/export consumers
  must operate on legs, not rows. The expansion rules are pinned per-row by
  `tests/unit/quant/test_catalog.py`, so neither list carries a count here.
- **Catalog spelling, not vendor spelling.** `quant_leg_symbols()` returns
  shorthand such as `VIX`, not `^VIX` / `$VIX` / `I:VIX`. Vendor resolution
  depends on the active source and belongs at the caller's fetch/cache seam.
- **Symbols verified against the live vendor.** `^RVX` is delisted and
  `^VIX3M` / `^VIX9D` are quote-only on Yahoo (one bar of history), so no
  term-structure ratio is offered — it would inner-join to a single bar.

## Invariants
- `row.symbol` is empty **iff** `row.available` is `False`.
- An unavailable row has a non-empty `unavailable_reason`; an available row
  has an empty one.
- `key` is unique across the whole catalog; `QuantGroup.key` is unique.
- Every row has a non-empty `name` and `description`.
- No available row's symbol contains whitespace, and every ratio row parses
  under `data/ratio_source.parse_ratio_symbol`.
- `available_symbols()` preserves catalog order and contains no
  case-insensitive duplicates.
- `quant_leg_symbols()` preserves first appearance order, contains no
  case-insensitive duplicates, and never returns numeric scale constants.

## Testing
`tests/unit/quant/test_catalog.py` pins every invariant above, plus:
symbol/ratio well-formedness through `data.ratio_source`, that each
scaled-symbol divisor is positive, that index shorthand resolves through
`data.index_aliases.resolve_symbol` without changing the scale constant, and
that `MOVE` is *not* used as bare shorthand. The extended tests also pin
`quant_leg_symbols()` expansion and de-duplication.

## Known limitations / Future work
- `GEX` / `DIX` have no feed. Wiring SqueezeMetrics would mean a new
  daily-only source emitting flat `O=H=L=C` bars.
- `TNX/IRX` divides by the 13-week discount rate, which approaches zero at
  the zero bound and makes the ratio large. It is still the canonical
  recession pair; read the level, not the magnitude.
- `quant_leg_symbols()` is the sandbox/export seam. It is consumed by
  `baskets.BUILTIN_BASKETS["quant"]` and
  `gui/export_cache_dialog.is_quant_entry`.

## Recent history
- Added `quant_leg_symbols()` for sandbox preload and cache-export filtering.
- Initial version: 7 groups, 31 rows, 2 of them disabled (`GEX`, `DIX`).
