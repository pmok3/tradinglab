# events/normalize.py — Spec

Last updated: 2026-11-16

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
- **Validate dates at the shared boundary.** Index dates are reconstructed as real `datetime.date` values before timestamp conversion. Invalid dates, including pandas `NaT` (whose `.date()` returns `NaT`), skip only their own row in both earnings and actions frames. Valid rows and their existing timezone/slot interpretation are preserved; an entirely invalid frame yields `[]`. This follows the existing quiet malformed-row policy and does not change provider-failure warnings.
- **9am rows classify DMH.** `slot_from_hour` is hour-only, so `09:00–09:59 ET` maps to DMH. `_extract_index_hour_et` still encodes the legacy +1 nudge for `09:30–09:59` rows before calling it.
- **Single-row both-cols.** `normalize_actions_df` emits a split record, then a cash record, from one row when both columns are populated. The final `ex_ts` sort is stable, so same-day split-before-cash order is preserved.
- **Output sorted ascending** by `ts` / `ex_ts` for binary-search-friendly order.

## Invariants
- `coerce_float(v)` never raises; failures map to `math.nan`.
- `normalize_*` never raise on bad rows — skip and continue.
- Empty/None DataFrame → `[]`.
- Invalid index dates cannot discard valid earnings, dividends, or splits from the same fetch.
- Output sort is stable (Python `sort` guarantee).

## Edge cases
- `Stock Splits=0` → no record. `Stock Splits=1.0` → no-op (no record). `Stock Splits=1.5` → rounds to `(2,1)` (documented v1 limitation).
- `MultiIndex` columns: `.columns` iteration works but lookups likely miss; records may have all-NaN fields. Acceptable degradation.

## Regression coverage
- `tests/unit/events/test_normalize.py`: mixed valid/`NaT` rows on both axes, entirely invalid frames, timezone-naive timestamps and UTC-date rollover.
- `tests/unit/events/test_yfinance_events.py`: bundle assembly retains both valid axes despite invalid dates; empty normalized output returns `None`.
- `check_b60_events_protocol_registry`: provider normalization retains valid events alongside `NaT` rows.
