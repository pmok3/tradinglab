# watchlists/signals.py — Spec

## Purpose
Headless **batch evaluator** for watchlist signal columns. Turns
`(symbols × WatchlistColumn[])` into per-cell values at the **latest
bar**, reusing [`scanner/engine`](../scanner/engine.spec.md)
`evaluate_field_at` — **no watchlist-specific math**, so watchlist
columns, scans, entries, and exits stay consistent. The GUI worker in
[`gui/watchlist_tab`](../gui/watchlist_tab.spec.md) runs it off the Tk
thread. See [`docs/WATCHLIST_COLUMNS.md`](../../../docs/WATCHLIST_COLUMNS.md).

## Public API
- `BarsProvider = Callable[[str, str, str], BarsNp | None]` —
  `(source, symbol, interval)` → bars (disk-cache backed).
- `@dataclass(frozen=True) class ColumnValue` — `raw: float | None`
  (sort key), `text: str` (display), `state: str`
  (`ok`/`loading`/`insufficient`/`error`).
- `class WatchlistSignalEvaluator`:
  - `__init__(*, bars_provider, source="yfinance")`.
  - `evaluate(symbols, columns) -> {symbol: {column_id: ColumnValue}}` —
    latest-bar evaluation, grouped + cached.
  - `invalidate(*, symbol=None)` — drop cached values on new bars / config
    change.
- `format_value(raw, fmt) -> str` — number / percent / multiplier / glyph.

## Dependencies
- Internal: [`watchlists/columns`](columns.spec.md) (`WatchlistColumn`),
  [`scanner/engine`](../scanner/engine.spec.md) (`evaluate_field_at`),
  [`scanner/fields`](../scanner/fields.spec.md), scanner
  `EvaluationContext`, `core/bars` / `disk_cache` (via the injected
  `bars_provider`).
- External: `dataclasses` (stdlib). No Tk / matplotlib.

## Design Decisions
- **Reuse the scanner engine, don't reimplement.** Each signal cell is
  `evaluate_field_at(col.ref, ctx, latest_index)`. `None` → `insufficient`.
- **Group by `(source, symbol, interval)`.** Load each group's bars once,
  build one scanner context, evaluate all columns sharing it — indicator
  memoization + one bars-load per symbol/interval keep cost linear.
- **Latest-bar semantics.** The last available index of each symbol's
  chosen interval; the watchlist never follows the chart interval — each
  column's interval is authoritative (default `1d`).
- **Value cache** keyed by `(source, symbol, interval, latest_ts,
  field_id, params, output_key, ref.interval, ref.symbol)` — recompute
  only when the bar or config changes.
  `invalidate` clears on new daily bars / config edits.
- **Injected `bars_provider`** keeps this headless + unit-testable
  offline (real provider = disk cache; tests pass fakes).
- **Formatting split from raw.** `evaluate` returns both a raw sort key
  and formatted `text`, so sorting is correct regardless of `fmt`.

## Invariants
1. A column is evaluated at the **last index** of its own interval; no
   future leakage.
2. Missing / insufficient data → `ColumnValue(raw=None, state="insufficient")`,
   never a fabricated number.
3. `evaluate` does not mutate its inputs.
4. A cached value is reused only while `(latest_ts, params, interval)` are
   unchanged.

## Data Flow / Algorithm
```text
evaluate(symbols, columns):
  groups = group columns by (symbol, interval)          # per symbol
  for (symbol, interval), cols in groups:
    bars = bars_provider(source, symbol, interval)
    if bars is None: cells = insufficient for all cols; continue
    ctx = scanner EvaluationContext(bars, latest_index)
    for col: raw = evaluate_field_at(col.ref, ctx, latest_index)
             cells[col.id] = ColumnValue(raw, format_value(raw, col.fmt), state)
  return {symbol: cells}
```

## Testing
- `tests/unit/test_watchlist_signals.py` — latest-index
  call into the engine; per-column interval respected; insufficient →
  `None`/`insufficient`; `format_value` presets; cache reuse +
  invalidation on latest-ts / config change; non-mutation. Fully offline
  with a fake `bars_provider`.

## Known limitations / Future work
- v1 is active-symbol only because this evaluator builds a plain scanner
  context and does not wire a `BarsRegistry`. Cross-symbol / RS columns
  (v2) will resolve via `FieldRef.symbol` + a registry on the context.
  The intraday **refresh cadence** lives in the GUI worker
  (`gui/watchlist_tab`), not here — this module is a pure function of its
  inputs.

## Recent history
- Implemented (latest-bar batch eval via the scanner engine, per-interval
  grouping, `(latest_ts, ColumnValue)` cache). Encodes the v1 decisions
  in `docs/WATCHLIST_COLUMNS.md`.
