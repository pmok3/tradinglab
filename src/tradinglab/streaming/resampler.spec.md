# `streaming/resampler.py` — design notes

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
  — raises `ValueError` on unsupported targets.
* `target_interval` / `target_minutes` — read-only properties exposing
  the configured interval string and minute width.
* `on_1m_tick(candle, *, forming) -> List[BarEvent]` — main entry.
* `current_forming() -> Optional[Candle]` — peek at the in-progress
  bucket.
* `reset()` — drop state on session boundary.
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
the bucket opening at 09:20. Floor division on `(t − anchor)`
minutes — Python's `//` floors toward −∞, which gives clean negative
buckets for pre-market.

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
