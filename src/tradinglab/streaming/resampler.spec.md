# `streaming/resampler.py` — design notes

Last updated: 2026-09-07

## Purpose

Layer −1 of the exit-strategies plan. The streaming source only emits
1-minute bars; higher intraday intervals (2m..4h) are materialised
on the fly so scanner conditions, exit triggers, and chart overlays
that reference those intervals can read a live `BarsBuffer` without
round-tripping to the historical adapter on every tick.

Daily / weekly / monthly are **out of scope**: they come from the
historical fetcher. This module only fills the intraday gap.

## Public API

* `BarResampler(target_interval: str, *, session_open_time=(9, 30))`
  — raises `ValueError` on unsupported targets or an out-of-range
  `session_open_time`.
  Optional `correction_buckets=2` enables current/previous-bucket minute
  replacement; default `0` preserves the history/scanner API and event behavior.
  Optional `session_segments=True` chooses US equity pre/regular/post anchors
  rather than the default fixed `session_open_time`.
* `target_interval` / `target_minutes` — read-only properties exposing
  the configured interval string and minute width.
* `on_1m_tick(candle, *, forming) -> List[BarEvent]` — main entry.
* `current_forming() -> Optional[Candle]` — peek at the in-progress
  bucket.
* `retained_events() -> tuple[BarEvent, ...]` — fresh oldest-first snapshots
  of correction-mode buckets, without advancing or mutating the resampler.
* `reset()` — drop state on session boundary.
* `bucket_start_for(stamp)` — expose the configured boundary calculation.
* `bucket_end_for(stamp)` — end-exclusive boundary, clipped at the session end
  when `session_segments=True`.
* `session_anchor(candle) -> datetime` — exchange-local start of the date/session
  segment, shared by REST and live aggregation. Session boundary constants come
  from `core.session_calendar`; aware times normalize through `core.timezones.ET`.
* `equity_bucket_bounds(stamp, interval)` — shared session-anchored `[start,end)`;
  rejects timestamps outside extended market hours.
* `covers(start, through, *, sealed=False)` — inclusive retained-minute coverage;
  `sealed=True` additionally requires authoritative `forming=False` contributions.
* `retained_minute_count` — bounded correction-mode diagnostic.
* `CorrectionUnavailable` — correction outside the retained known minutes;
  chart callers must reconcile history instead of guessing missing contributions.
* `BarEvent(closed, candle, source_minute_count)` — frozen dataclass.
* `supported_intervals() -> Tuple[str, ...]` — canonical list of
  `target_interval` values accepted by `BarResampler(...)`. Used by
  the streaming dispatcher and the scanner / exits layers to gate
  intraday interval choices to what the resampler can actually
  materialise from 1m ticks.

Supported targets: `2m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h`.

## Bucket alignment

Buckets are anchored at the configured session open (default `09:30`)
and walk forwards / backwards in `target_min` steps. A 5m candle at
09:25 belongs to the bucket opening at 09:25; at 09:23 it belongs to
the bucket opening at 09:20. Whole-minute timestamps use floor
division on `(t − anchor)` minutes; Python's `//` floors toward −∞,
which gives clean negative buckets for pre-market. If a sub-minute
timestamp slips through, it is rounded to the nearest minute before
bucket selection.

## Aggregation rules

* `open` = first merged 1m's open.
* `high` / `low` = max / min across all merged 1m bars.
* `close` = last merged 1m's close.
* `volume` = sum.
* `session` = most-common across merged bars; tiebreak = last bar's
  session (covers the rare regular↔post boundary case).

## Forming-bar correctness

The 1m `Candle` is mutable; the streaming pipeline reuses one
instance for successive `forming=True` updates. The aggregator never
caches a snapshot of the pending 1m's fields — it stores a *reference*
in `_pending_1m` and re-reads `.open/.high/.low/.close/.volume` every
time the higher-interval candle is built. Locked (closed-1m)
contributions, by contrast, are eagerly copied out into scalar state.

On bucket rollover any still-pending 1m is treated as locked at its
last seen values before the bucket is sealed, so a missing `forming=False`
event for the boundary minute can't leak data forward.

Out-of-order ticks whose bucket is older than the current bucket are
ignored and emit no events.

### Opt-in correction mode

With `correction_buckets=2`, whole-minute contributions are copied and keyed by
timestamp in exactly the current and previous target buckets. Repeated final
minutes replace rather than add; final minutes cannot be downgraded by later
forming updates. Recomputing through the existing scalar reducer allows high
to shrink, low to rise, and volume/open/close/session to be corrected.
Known previous-bucket corrections emit `closed=True`; unknown or evicted older
minutes raise `CorrectionUnavailable`. No arbitrary historical insertion.
`reset()` discards contributions and finality. Retention is bounded by twice
the target interval's minute count. Default callers remain unchanged.

Session-segment anchors are pre 04:00, regular 09:30 and post 16:00 ET. Each
bucket ends no later than its segment boundary (regular 15:30 hourly bucket
ends at 16:00, not 16:30). The next date/session cannot inherit its contributions.

## Testing

`tests/streaming/test_resampler.py` covers default history behavior and opt-in
replacement, finality, extrema corrections, retention, and reset.
