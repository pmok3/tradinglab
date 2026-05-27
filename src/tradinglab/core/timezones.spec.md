# timezones.py — Spec

## Purpose
Single source of truth for the US-Eastern :class:`zoneinfo.ZoneInfo`.
Replaces 11+ ad-hoc ``ZoneInfo("America/New_York")`` constructions
(some wrapped in try/except for missing-tzdata environments, with
subtly drifting fallback policies) with one cached primitive.

## Public API
- `ET: tzinfo | None` — eagerly resolved at import. `None` when
  `tzdata` is missing. Most callers import this.
- `get_et() -> tzinfo | None` — lazy accessor; identical to `ET`
  after first call (cached). Slow-path for "I might be imported
  before tzdata is installed" callers.
- `now_et() -> datetime` — current wall-clock time in ET. Falls
  back to a naive (no-tz) datetime when tzdata is missing.
- `to_et(epoch_seconds: float) -> datetime` — convert a UTC epoch
  to an ET-aware datetime. Falls back to UTC-aware when tzdata is
  missing.

## Dependencies
- Internal: none.
- External: `zoneinfo` (stdlib, Python 3.9+); `datetime` (stdlib).

## Design Decisions
- **Eager module-level `ET` constant.** Most call sites historically
  wrote `from zoneinfo import ZoneInfo; ET = ZoneInfo("...")` at
  module scope. The same shape with `from .core.timezones import ET`
  lets us migrate with zero behavioural change.
- **Cached after first resolution.** Constructing `ZoneInfo` is
  cheap (microseconds), but importing tzdata is non-trivial; caching
  amortises across the long-running session.
- **Returns `None` on missing tzdata, not raises.** Matches the prior
  consensus fallback in `app.py::_intraday_session_open` (which
  returns `True` conservatively when zoneinfo is unavailable). Callers
  branch on `et is None` to choose their own degraded behaviour.
- **`now_et()` and `to_et()` are convenience helpers, not the
  primary surface.** Most call sites want a `tzinfo` object to pass
  into `datetime.fromtimestamp(ts, tz=)` or `datetime.now(tz=)`. The
  helpers exist for the few call sites where the imperative shape is
  cleaner.
- **No DST-aware date arithmetic helpers here.** Those live in
  `strategy_tester/evaluator.py::_compute_et_arrays` because they're
  vectorized via numpy (CLAUDE.md §7.14). This module is the
  "give me ET" layer, not the "compute things in ET" layer.

## Invariants
- `ET is get_et()` after the module has been imported (the eager
  module-level read populates the cache).
- `get_et()` returns the SAME object on every call within one process
  — never re-constructs.
- `to_et(0).tzinfo is not None` is True when tzdata is installed.

## Testing
- `tests/core/test_timezones.py` — cover: ET non-None when tzdata
  installed; cached identity across calls; now_et returns tz-aware
  datetime; to_et roundtrip; graceful behaviour when ZoneInfo
  raises (simulate via monkeypatch).
