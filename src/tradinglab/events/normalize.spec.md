# events/normalize.py — Spec

## Purpose
Provider-agnostic, column-tolerant translation between pandas-DataFrame-shaped provider payloads and the canonical `EarningsRecord` / `DividendRecord` types. Lifted out of `events.yfinance_events` so the variant matrix is unit-testable without yfinance.

## Public API
- `normalize_earnings_df(df, *, symbol, source) -> List[EarningsRecord]`
- `normalize_actions_df(df, *, symbol, source) -> List[DividendRecord]`
- `coerce_float(v)` — None/NaN/inf-tolerant float coercion.
- `date_to_midnight_ms(d)` — date → UTC midnight ms.
- `slot_from_hour(hour_et)` — hour-only slot classifier: `<9` → `BMO`, `>=16` → `AMC`, otherwise `DMH`.
- `EARNINGS_EST_VARIANTS`, `EARNINGS_ACT_VARIANTS`, `REVENUE_EST_VARIANTS`, `REVENUE_ACT_VARIANTS` — column-name fallback tables, exposed so smoke checks lock in the matrix.

## Dependencies
Internal: `.base`. External: none at import. Pandas-shape duck-typed at call time — any object exposing `.columns`, `.iterrows()`, `.empty` works.

## Design Decisions
- **Pure / no I/O.** Trivially unit-testable.
- **Case-insensitive column lookup** preserving original names for `row[col]` indexing (pandas is case-sensitive at the row level).
- **First-match wins** in `_resolve_column`; variant tables ordered most-recent-first.
- **Missing columns are not errors.** Missing earnings columns → NaN fields; missing dividend columns → empty output.
- **9am rows classify DMH.** `slot_from_hour` is hour-only, so `09:00–09:59 ET` maps to DMH. `_extract_index_hour_et` still encodes the legacy +1 nudge for `09:30–09:59` rows before calling it.
- **Single-row both-cols.** `normalize_actions_df` emits a split record AND a cash record from one row when both columns are populated, in that order (stable by emission, not just `ex_ts`).
- **Output sorted ascending** by `ts` / `ex_ts` for binary-search-friendly order.

## Invariants
- `coerce_float(v)` never raises; failures map to `math.nan`.
- `normalize_*` never raise on bad rows — skip and continue.
- Empty/None DataFrame → `[]`.
- Output sort is stable (Python `sort` guarantee).

## Edge cases
- `Stock Splits=0` → no record. `Stock Splits=1.0` → no-op (no record). `Stock Splits=1.5` → rounds to `(2,1)` (documented v1 limitation).
- `MultiIndex` columns: `.columns` iteration works but lookups likely miss; records may have all-NaN fields. Acceptable degradation.
