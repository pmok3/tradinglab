# `strategy_tester/interval_compat.py`

Pre-Run guard that detects when a strategy references an **intraday-only**
indicator while the Strategy Tester run interval is daily / weekly / monthly.
Such a Run silently produces **zero trades** (the indicator is NaN every bar,
so any condition reading it is *unknown* under tri-valued logic and never
fires) — this module surfaces the mismatch so the GUI can block the Run with
an explanatory popup instead.

Audit `intraday-interval-guard`.

## Surface

- `incompatible_indicators_for_interval(entry, exit, interval) -> list[tuple[str, str]]`
  — **Strategy Tester** check. The run normalizes every condition to the single
  `cfg.interval`, so every referenced indicator is checked against that one
  interval. Returns `[(display_name, reason), ...]`; empty ⇒ safe to run.
  De-duplicated by display name (first reason wins), order-stable. Indicator
  set from `warmup.collect_referenced_indicator_kinds`.
- `incompatible_arming_problems(entry, *, available_intervals=None, fallback_interval="1m") -> list[str]`
  — **live + sandbox arming** check. Unlike the tester, the live/sandbox
  evaluators respect each condition's **own** interval, so this resolves a
  per-reference interval (`field.interval or condition.interval or
  trigger.interval or fallback_interval`) and checks each against it. Returns
  human-readable problem strings; empty ⇒ safe to arm.
  - `available_intervals=None` (live): any interval is fetchable, so the only
    problem is an intraday-only indicator pinned to a non-intraday interval
    (a 5m VWAP strategy stays armable; a 1d-authored VWAP strategy is blocked).
  - `available_intervals` non-None (sandbox): the only intervals the session
    can serve. A condition tree that needs an interval outside this set is
    flagged (`needs <pretty> bars, which this sandbox session doesn't
    provide`) even if it has no indicators (e.g. a 5m builtin breakout in a
    1d-only sandbox). An indicator whose interval is already flagged this way
    is not double-reported.
  - Only triggers with a condition tree (INDICATOR) are inspected; a MARKET
    trigger fires on the tick regardless of interval and is **never** flagged.
    SCANNER_ALERT scans are on disk and not walked.

## Design Decisions

- **Single source of truth for availability** is
  `indicators.base.factory_is_available_for(factory, interval, params)` —
  which consults each factory's `is_available_for` method. This is the same
  helper the chart Add-Indicator menu and `indicators.config` use, so the
  tester, the menu, and rendering all agree on what "intraday-only" means.
  VWAP, RVOL (cumulative / time-of-day modes), RRVOL, and Prior Day High/Low
  declare themselves via `intraday_only(interval)`; RVOL's `simple` mode is
  params-aware and stays available on daily (so it is **not** flagged).
- **Indicator set comes from**
  `warmup.collect_referenced_indicator_kinds(entry, exit)` — shared with the
  warmup sizer so both walk the identical surface (entry INDICATOR-trigger
  condition tree + enabled exit-leg INDICATOR triggers + CHANDELIER triggers).
- **Fail-open.** Unknown `kind_id`s and indicators without an
  `is_available_for` declaration are treated as available — only an explicit
  "not available on this interval" blocks the Run. This keeps user-plugin
  indicators working unless they opt into an availability rule.
- **SCANNER_ALERT entries are not walked** — the referenced scan lives on
  disk and is resolved later by the runner; matching the warmup walker's
  documented limitation. A scanner-alert entry that references an
  intraday-only field on a daily Run is not currently caught here.

## Dependencies

- `indicators.base.factory_by_kind_id` / `factory_is_available_for`
- `strategy_tester.warmup.collect_referenced_indicator_kinds`
- `scanner.model.{Group, Condition, FieldRef}` (the per-reference walk)
- `entries.model.EntryStrategy`, `exits.model.ExitStrategy` (types only)

## Consumers

- `gui/strategy_tab.py:_on_run_clicked` — calls
  `incompatible_indicators_for_interval` before starting a Run; a non-empty
  result raises a `messagebox.showerror` popup and aborts the Run (never flips
  the running UI on).
- `gui/entries_tab.py:_on_arm` — calls `incompatible_arming_problems` before
  arming; a non-empty result raises a `messagebox.showerror` and skips the arm.
  The sandbox `available_intervals` come from `_sandbox_intervals_provider`
  (wired by `gui/entries_app.py:_sandbox_arming_intervals` to the active
  `SandboxController.display_intervals`, or `None` when live).

## Tests

- `tests/unit/strategy_tester/test_interval_compat.py` — both functions.
  Tester check: VWAP-on-1d flagged, VWAP-on-5m clean, RVOL-simple-on-1d clean,
  pure-builtin breakout clean, blank interval clean, exit-side VWAP flagged,
  de-dup, unknown kind fail-open. Arming check: 5m VWAP clean live but blocked
  in a 1d sandbox / clean in a 5m sandbox; daily VWAP blocked live + sandbox;
  daily EMA clean in 1d sandbox; MARKET never flagged; 5m builtin breakout
  blocked in 1d sandbox but clean live.
- `tests/gui/test_entries_tab.py` — `_on_arm` wiring: daily VWAP blocked live,
  5m VWAP armed live, 5m strategy blocked in a 1d sandbox, MARKET armed in a
  1d sandbox (no false positive).
