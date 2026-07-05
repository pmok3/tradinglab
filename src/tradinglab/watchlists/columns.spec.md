# watchlists/columns.py — Spec

## Purpose
Defines the **watchlist column model**: `WatchlistColumn` (a `"system"`
column like `last` / `next_earn`, or a `"signal"` column backed by a
scanner [`FieldRef`](../scanner/model.spec.md)), plus serialization,
validation, and compact header labels. Pure data — no Tk, no compute.
Consumed by [`watchlists/storage`](storage.spec.md) (persistence),
[`watchlists/signals`](signals.spec.md) (evaluation), and
[`gui/watchlist_columns_dialog`](../gui/watchlist_columns_dialog.spec.md)
(editing). See [`docs/WATCHLIST_COLUMNS.md`](../../../docs/WATCHLIST_COLUMNS.md).

## Public API
- `SYSTEM_COLUMN_IDS: tuple[str, ...]` — `("ticker","last","change","change_pct","next_earn")`.
- `KIND_SYSTEM` / `KIND_SIGNAL` / `LOCKED_COLUMN_ID` (`"ticker"`).
- `@dataclass(frozen=True) class WatchlistColumn` — `kind`, `id`,
  `ref: FieldRef | None`, `label`, `width`, `anchor`, `fmt`.
- `default_columns() -> list[WatchlistColumn]` — today's fixed 5.
- `column_to_dict(col)` / `column_from_dict(dict) -> WatchlistColumn | None`.
- `columns_to_json(cols)` / `columns_from_json(data) -> list[WatchlistColumn]`.
- `validate_columns(cols) -> list[WatchlistColumn]` — `ticker` first +
  locked, deduped, invalid dropped.
- `signal_column_id(ref) -> str` — deterministic Treeview column id for a
  signal ref (`(field_id, params, interval, output_key, symbol)`).
- `header_label(col) -> str` — compact `RVOL(20,5m)` / `ADX(14,D)` / `Chg%`.

## Dependencies
- Internal: [`scanner/model`](../scanner/model.spec.md) (`FieldRef`).
- External: `dataclasses` (stdlib). No Tk / matplotlib / numpy.

## Design Decisions
- **System vs signal columns.** Legacy price columns are `"system"`
  (removable, so the owner can reclaim space); user-added signals carry a
  `FieldRef`. `ticker` is a system column that is **locked + first**.
- **Raw value ≠ display.** `fmt` chooses the cell presentation; the raw
  numeric value (for sorting) is produced by the evaluator, not stored on
  the column. Keeps sort correct regardless of formatting.
- **Stable signal id from the `FieldRef`.** A signal column's `id`
  derives deterministically from `(field_id, params, interval,
  output_key, symbol)` so it is a stable Treeview column key and
  dedupe target.
- **Drop-invalid, never crash.** `columns_from_json` / `validate_columns`
  tolerate junk (hand-edited files, a deleted custom indicator): unknown
  columns are dropped, not fatal.
- **Signal ids preserve symbol pins.** `FieldRef.symbol` participates
  in serialization and dedupe. The v1 signal evaluator still lacks a
  `BarsRegistry`, so non-active-symbol relative columns remain future
  work even though the column model can store them.

## Invariants
1. After `validate_columns`, `ticker` is present exactly once, first, and
   `kind == "system"`.
2. Column ids are unique within a list (dedupe keeps the first).
3. `column_to_dict` → `column_from_dict` round-trips every field.
4. `kind ∈ {"system","signal"}`; a `"signal"` column has a non-`None`
   `ref`, a `"system"` column has `ref is None`.

## Data Flow / Algorithm
```text
validate_columns(cols):
  keep only kind in {system, signal}, valid ref for signals
  dedupe by id (first wins)
  ensure exactly one ticker, force it to index 0 (insert default if absent)
  return ordered list
```

## Testing
- `tests/unit/test_watchlist_columns.py` — round-trip
  serialize; `from_dict` tolerates junk → `None`; `validate` forces
  `ticker` first + locked, dedupes, drops invalid `FieldRef`s;
  `header_label` grammar (params + interval); `default_columns()` equals
  today's 5.

## Known limitations / Future work
- v1 stores no per-column color rule (per-cell heat is deferred; Tk
  `Treeview` limitation). v2 relative/RS columns reuse `FieldRef.symbol`.

## Recent history
- Implemented (model + serialization + validation + header labels). A
  signal column's `id` is `signal_column_id(ref)`; `ticker` is forced
  first + locked. Encodes the v1 decisions in `docs/WATCHLIST_COLUMNS.md`.
