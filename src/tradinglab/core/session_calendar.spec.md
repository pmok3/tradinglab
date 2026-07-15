# session_calendar.py — Spec

## Purpose
Single source of truth for US-equity **trading-session boundaries**
(pre / regular / post) and the RTH predicates. Replaces ~7 independent
re-hardcodings of the `09:30`/`16:00` boundaries and the four
differently-named `include_extended` booleans (`prepost` /
`include_extended` / `include_extended_hours` / `include_ext`). Direct
analogue of `core.timezones` (which consolidated `ZoneInfo` construction)
and `core.view_intent` (which consolidated the render-preservation
booleans).

## Public API
### Boundary constants (the ONE definition of the session edges)
- Minute-of-day: `PRE_OPEN_MIN=240`, `RTH_OPEN_MIN=570`,
  `RTH_CLOSE_MIN=960`, `POST_CLOSE_MIN=1200`, `RTH_SPAN_MIN=390`.
- Second-of-day: `RTH_OPEN_SEC=34200`, `RTH_CLOSE_SEC=57600` (consumed
  by the vectorized evaluator kernel).
- `datetime.time` forms: `PRE_OPEN_TIME=04:00`, `RTH_OPEN_TIME=09:30`,
  `RTH_CLOSE_TIME=16:00`, `POST_CLOSE_TIME=20:00`.

### Functions
- `classify_session(hour, minute) -> str` — bucket a wall-clock ET time
  into `"pre"` / `"regular"` / `"post"` using **half-open** intervals
  (regular `[09:30, 16:00)`, post `[16:00, 20:00)`). Bar-tagging
  convention (flows into `Candle.session`).
- `classify_session_arr(hours, minutes) -> list[str]` — vectorized twin,
  bit-for-bit identical to the scalar. Lazily imports numpy; returns
  shared label objects.
- `is_regular_session(dt) -> bool` — trading-engine RTH membership,
  **closed** interval `[09:30, 16:00]`, Mon–Fri. (The 16:00 bar counts.)
- `is_rth_now(now=None) -> bool` — wall-clock RTH check, **half-open**
  `[09:30, 16:00)`, Mon–Fri. Missing tzdata (and `now is None`) ⇒
  conservative `True`.
- `market_window(include_extended) -> (time, time)` — `(04:00, 20:00)`
  when extended, else `(09:30, 16:00)`.

## Dependencies
- Internal: `core.timezones.ET` (function-local import inside
  `is_rth_now` only).
- External: `datetime` (stdlib); `numpy` (lazy, inside
  `classify_session_arr`).

## Design Decisions
- **Two RTH predicates on purpose.** `classify_session` (half-open, so
  16:00 → `"post"`) is the data-layer bar-tagging convention;
  `is_regular_session` (closed, so 16:00 → regular) is the trading
  engine's membership test; `is_rth_now` (half-open) is the scheduler's
  wall-clock check. They share the boundary numbers and differ ONLY at
  exactly 16:00 — pinned by a dedicated test so the difference stays
  intentional, not accidental drift.
- **`is_rth_now` uses function-local `datetime` + `ET` imports.** The
  `updates` / `gui.watchlist_tab` delegating call sites are unit-tested
  by patching `datetime.datetime` and `core.timezones.ET` at call time;
  resolving both inside the function preserves those tests after
  delegation.
- **`classify_session` moved here from `constants`.** `constants`
  re-exports both classifiers so the ~8 existing
  `from .constants import classify_session` importers are untouched. The
  old "keep the two functions in lockstep" hazard is gone: both read the
  same module-level boundary constants.
- **Second-of-day + minute-of-day + `time` forms all exposed.** Callers
  work in different units (evaluator kernel in seconds, volume-tod
  overlay in minutes, schedulers in `datetime.time`); one module owns
  all three so they cannot drift.
- **No holiday calendar.** Matches prior behaviour — holidays are left
  to the data layer / user-supplied data.

## Invariants
- `RTH_OPEN_SEC == RTH_OPEN_MIN * 60` and
  `RTH_CLOSE_SEC == RTH_CLOSE_MIN * 60`.
- `RTH_SPAN_MIN == RTH_CLOSE_MIN - RTH_OPEN_MIN == 390`.
- `classify_session_arr` is bit-for-bit identical to the scalar
  `classify_session` for every (hour, minute) of a day.
- `classify_session` (== "regular") and `is_regular_session` agree for
  every RTH minute EXCEPT exactly 16:00 (closed vs half-open close).
- `is_rth_now(now=None)` returns `True` when `core.timezones.ET is None`.
- The RTH-open boundary literals (`9*60+30`, `9*3600+30*60`) appear in
  no production module other than `core/session_calendar.py`.

## Testing
- `tests/core/test_session_calendar.py` — boundary-constant values +
  internal consistency; `classify_session` half-open boundaries;
  `classify_session_arr` scalar equivalence + shared labels + empty
  input; `is_regular_session` closed-interval + weekend; `is_rth_now`
  half-open + weekend + injected-now + missing-tzdata + call-time
  clock-patch; `market_window` regular/extended; the intentional
  half-open-vs-closed difference at 16:00; adoption invariant that the
  RTH-open literals live only here.
