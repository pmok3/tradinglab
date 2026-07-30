# timezones.py — Spec

## Purpose
Single source of truth for :class:`zoneinfo.ZoneInfo` resolution.
Replaces ad-hoc ``ZoneInfo("America/New_York")`` and display-timezone
constructions (some wrapped in try/except for missing-tzdata
environments, with subtly drifting fallback policies) with cached
primitives.

## Public API
- `ET: tzinfo | None` — eagerly resolved at import. `None` when
  `tzdata` is missing. Most callers import this.
- `get_et() -> tzinfo | None` — lazy accessor; identical to `ET`
  after first call (cached). Slow-path for "I might be imported
  before tzdata is installed" callers.
- `get_zoneinfo(name: str | None) -> tzinfo | None` — cached generic
  IANA-zone resolver for user-selectable display timezones. Blank,
  invalid, or unavailable names return `None`; `"America/New_York"`
  delegates to `get_et()`. Non-ET lookups are kept in a bounded
  `LRUDict(maxsize=64)`.
- `now_et() -> datetime` — current wall-clock time in ET. Falls
  back to a naive (no-tz) datetime when tzdata is missing.
- `to_et(epoch_seconds: float) -> datetime` — convert a UTC epoch
  to an ET-aware datetime. Falls back to UTC-aware when tzdata is
  missing.

### UTC timestamp minting
- `UTC_ISO_FORMAT` / `UTC_COMPACT_FORMAT` — the two strftime patterns.
- `utc_now_iso() -> str` — `YYYY-MM-DDTHH:MM:SSZ`. On-disk
  `created_at` / `updated_at` / `finished_at` for the entries, exits,
  scanner and strategy_tester models.
- `utc_now_compact() -> str` — `YYYYMMDDTHHMMSSZ`. Filesystem-safe (no
  colons); strategy-tester run-directory names.
- `utc_now_naive_iso() -> str` — naive `YYYY-MM-DDTHH:MM:SS`, no offset
  suffix, no microseconds. On-disk format for `drawings` records and
  sandbox-resume checkpoints.

### Epoch normalization
- `MS_EPOCH_THRESHOLD = 1e12` — any epoch value at or above this is
  unambiguously milliseconds (as seconds it would be year 33,658).
- `normalize_epoch_to_seconds(ts) -> float` — ms-or-seconds in, seconds
  out, decided by magnitude.

## Dependencies
- Internal: `core.lru_dict.LRUDict` for the generic timezone cache.
- External: `zoneinfo` (stdlib, Python 3.9+); `datetime` (stdlib).

## Design Decisions
- **Eager module-level `ET` constant.** Most call sites historically
  wrote `from zoneinfo import ZoneInfo; ET = ZoneInfo("...")` at
  module scope. The same shape with `from .core.timezones import ET`
  lets us migrate with zero behavioural change.
- **Cached after first resolution.** Constructing `ZoneInfo` is
  cheap (microseconds), but importing tzdata is non-trivial; caching
  amortises across the long-running session.
- **Generic zone cache is bounded.** Display timezone names come from
  user settings; keep the cache at 64 entries so malformed/manual
  churn cannot grow process memory for the lifetime of the app.
- **Returns `None` on missing tzdata, not raises.** Matches the prior
  consensus fallback in `app.py::_intraday_session_open` (which
  returns `True` conservatively when zoneinfo is unavailable). Callers
  branch on `et is None` to choose their own degraded behaviour.
- **Production zoneinfo imports are centralized here.** Callers that
  need ET use `ET` / `get_et`; callers that need an arbitrary user
  display timezone use `get_zoneinfo`. This keeps missing-tzdata and
  invalid-name behavior consistent.
- **`now_et()` and `to_et()` are convenience helpers, not the
  primary surface.** Most call sites want a `tzinfo` object to pass
  into `datetime.fromtimestamp(ts, tz=)` or `datetime.now(tz=)`. The
  helpers exist for the few call sites where the imperative shape is
  cleaner.
- **No DST-aware date arithmetic helpers here.** Those live in
  `strategy_tester/evaluator.py::_compute_et_arrays` because they're
  vectorized via numpy (CLAUDE.md §7.14). This module is the
  "give me ET" layer, not the "compute things in ET" layer.
- **Three UTC-minting formats are named, not unified.** Eight sites
  across five subsystems hand-rolled "UTC now, as a string", landing on
  three different on-disk formats via two different implementations
  (`time.strftime(..., time.gmtime())` in the entries / exits /
  strategy_tester models; `datetime.now(timezone.utc).strftime(...)` in
  the scanner model). All three formats are load-bearing — existing
  saved strategies, scans, runs, drawings and resume checkpoints depend
  on their exact shape — so this module exposes each one under its own
  name rather than collapsing them. What was removed is the repeated
  *implementation*, not the format divergence.
- **The minting helpers resolve `_dt.datetime` at CALL time.** They use
  `import datetime as _dt` + attribute lookup instead of a module-level
  `from datetime import datetime` binding, because
  `tests/unit/test_datetime_utcnow_deprecation.py::TestMockedClockOutput`
  freezes the clock by patching the `datetime` attribute on the stdlib
  module object. An import-time binding would capture the real class and
  silently ignore that patch.
- **`normalize_epoch_to_seconds` lives here, not in `strategy_tester`.**
  The ms-vs-seconds heuristic had four copies (twice in
  `strategy_tester/screenshot.py`, plus `backtest/heatmap.py` and
  `backtest/heatmap_provider.py`), each carrying its own bare `1e12`
  literal. It is the heuristic behind the "every trade screenshot renders
  the same window" bug (CLAUDE.md §7.7), so four independently-editable
  thresholds was the highest-risk of the remaining duplicates. Placed in
  this module because it is timestamp-domain and dependency-free.

## Invariants
- `ET is get_et()` after the module has been imported (the eager
  module-level read populates the cache).
- `get_et()` returns the SAME object on every call within one process
  — never re-constructs.
- `get_zoneinfo("America/New_York") is get_et()`.
- Non-ET `get_zoneinfo` cache never grows past 64 entries.
- No production module outside `core/timezones.py` imports `zoneinfo`
  directly.
- `to_et(0).tzinfo is not None` is True when tzdata is installed.

## Consumers (UTC minting)
- `utc_now_iso` — `entries/model.py::_utcnow_iso`,
  `exits/model.py::_utcnow_iso`, `scanner/model.py::_utcnow_iso`,
  `strategy_tester/model.py::_utcnow_iso`,
  `strategy_tester/runner.py` (`finished_at`, 3 sites).
- `utc_now_compact` — `strategy_tester/storage.py::_run_dir_stamp`,
  `strategy_tester/runner.py` (`started_iso`, 2 sites).
- `utc_now_naive_iso` — `drawings/store.py::_now_iso`,
  `drawings/model.py::make_hline_drawing`,
  `backtest/sandbox_resume.py::now_iso`.
- `normalize_epoch_to_seconds` —
  `strategy_tester/screenshot.py::_normalize_ts_to_seconds` and
  `::_format_et_timestamp_from_ms`, `backtest/heatmap.py::_to_seconds`,
  `backtest/heatmap_provider.py::_to_seconds`.

## Testing
- `tests/core/test_timezones.py` — cover: ET non-None when tzdata
  installed; cached identity across calls; now_et returns tz-aware
  datetime; generic timezone lookup; bounded generic cache; to_et
  roundtrip; graceful behaviour when ZoneInfo raises (simulate via
  monkeypatch); source invariant that production ZoneInfo imports stay
  centralized here.
- `tests/unit/test_datetime_utcnow_deprecation.py` — pins the three
  on-disk minting formats and the call-time clock read (patches
  `core.timezones._dt.datetime` and asserts through the per-module
  `_now_iso` / `now_iso` delegators).
