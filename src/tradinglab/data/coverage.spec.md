# data/coverage.py — Spec

> Design contract: [`docs/TARGETED_FETCH.md`](../../../docs/TARGETED_FETCH.md).
> The module is implemented + unit-tested; it is **not yet consumed** by the
> drilldown fetch path (Phase-1 wiring pending).

## Purpose
Per-`(source, ticker, interval)` **fetch-coverage record** — a small sidecar
(next to each `disk_cache` JSONL) tracking which date ranges have actually
been fetched and how far back the provider's data goes. Enables the targeted /
on-demand intraday fetch to anchor its page-span window against the real data
boundary, skip already-covered ranges, and distinguish *loading* vs *no-bars*
vs *provider-exhausted* in the UI.

## Public API
- `SCHEMA_VERSION: int = 1`; `COVERAGE_SUFFIX = ".coverage.json"`.
- `@dataclass CoverageRecord`: `data_start_ts: int | None`,
  `exhausted_start: bool`, `segments: list[tuple[int,int]]` (merged, sorted,
  half-open `[start_ts, end_ts)` epoch-second ranges), `version: int`.
- `load(source, ticker, interval, *, root=None) -> CoverageRecord` — empty
  record on missing/corrupt sidecar; never raises.
- `save(source, ticker, interval, record, *, root=None) -> None` — atomic,
  best-effort; never raises.
- `bootstrap_from_cache(source, ticker, interval, *, root=None) -> CoverageRecord`
  — seed one segment from an existing JSONL's min/max bar ts (so we never
  re-fetch what's on disk).
- `record_fetch(source, ticker, interval, req_start, req_end, returned_start, returned_end, *, root=None) -> CoverageRecord`
  — merge `[req_start, req_end)`; learn `data_start_ts` + set `exhausted_start`
  when the provider returned nothing older than requested.
- `missing_ranges(record, start, end) -> list[tuple[int,int]]` — sub-ranges of
  `[start,end)` not yet covered.
- `covered(record, start, end) -> bool`.
- `data_start(record) -> int | None`.

## Dependencies
- Internal (planned): `.. disk_cache` (path + JSONL min/max ts for bootstrap),
  `.. paths` / `.. core.io_helpers` (atomic JSON write).
- External: stdlib only (`dataclasses`, `pathlib`, `json`).

## Design Decisions
- **Sidecar, not a rewrite of `disk_cache`.** The JSONL stays the single merged
  candle store; coverage is additive metadata so existing caches keep working
  (bootstrap seeds "present, coverage = its min/max span").
- **Half-open `[start, end)` epoch-second segments**, merged + sorted, so
  `covered` / `missing_ranges` are simple interval arithmetic.
- **Three distinguishable states** for the UI (see `docs/TARGETED_FETCH.md`
  §4.3): *loading* (∈ `missing_ranges`, fetch in flight), *no bars for range*
  (segment covers it, no candles present), *provider-exhausted* (older than
  `data_start_ts` with `exhausted_start`).
- **Never raises on I/O.** A corrupt/absent sidecar degrades to an empty record
  — a coverage bug must never break a fetch.

## Invariants (pinned by tests)
- `segments` is always merged + sorted, non-overlapping, half-open.
- `covered(rec, s, e)` ⟺ `missing_ranges(rec, s, e) == []`.
- `record_fetch` is idempotent for an already-covered range (no duplicate/growth).
- `load` / `save` round-trip a record unchanged.
- A returned-start ≥ `_DATA_START_MARGIN_S` (7d) later than requested learns
  `data_start_ts` + sets `exhausted_start`; a weekend-sized gap does not.

## Testing
- `tests/unit/data/test_coverage.py` (14 tests) — segment merge, `missing_ranges`,
  `covered`, `data_start` watermark learning (+ the 7-day margin), `record_fetch`
  idempotency, load/save round-trip + corrupt-sidecar → empty, path sanitization,
  bootstrap from a JSONL.

## Recent history
- **Implemented** — segment merge, `missing_ranges`/`covered`, watermark
  learning in `record_fetch`, atomic load/save, JSONL bootstrap. 14 unit tests.
  Not yet consumed by the drilldown path (Phase-1 wiring pending).
- **API skeleton** — dataclass + constants + entry points defined; encoded the
  v1 decisions in `docs/TARGETED_FETCH.md`.
