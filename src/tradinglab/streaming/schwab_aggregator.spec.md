# streaming/schwab_aggregator.py — Spec

## Purpose
Pure, threadless aggregator that rolls Schwab streamer events into 1-minute `Candle`s. Two services are decoded here:
* **LEVELONE_EQUITIES** (sub-minute quote/trade ticks) → drives an in-progress bar.
* **CHART_EQUITY** (already-closed 1-minute bars from the tape) → the authoritative source for sealed minutes.

The module owns no threads, sockets, or `time.time()` — consumers feed it parsed dicts plus a wall-clock `datetime`. Drives unit tests directly.

## Public API
- `LEVELONE_FIELDS: Dict[str, str]` — Schwab numeric-field-ID → logical name. Subset we consume: symbol / bid_price / ask_price / last_price / bid_size / ask_size / total_volume / trade_time_ms.
- `decode_levelone_content(content: Mapping) -> Dict[str, Any]` — translate one wire content dict to logical names; missing keys stay missing.
- `CHART_EQUITY_FIELDS: Dict[str, str]` — symbol / sequence / open / high / low / close / volume / chart_time_ms.
- `decode_chart_equity_content(content: Mapping) -> Dict[str, Any]`.
- `chart_equity_to_candle(decoded: Mapping, *, tz=timezone.utc) -> Optional[Candle]` — returns `None` if any required OHLCV/timestamp field is missing; otherwise coerces fields into a `Candle`.
- `class MinuteBarBuilder(seed_close: float)` — stateful per-symbol aggregator.
  - `open_initial_bar(now: datetime) -> Tuple[str, Candle]` — emits the first `("rollover", Candle)` event seeded at `seed_close`.
  - `apply_levelone(decoded: Mapping, *, now: datetime) -> List[Tuple[str, Candle]]` — applies one tick. Returns 0+ `("tick", Candle)` and/or `("rollover", Candle)` events (multiple if a single update crossed minute boundaries).
  - `maybe_rollover(now: datetime) -> List[Tuple[str, Candle]]` — boundary check without a tick; used by a per-source clock thread so quiet symbols still roll.

## Dependencies
- Internal: `..constants.{classify_session, floor_to_interval}`, `..models.Candle`.
- External: stdlib only (`dataclasses`, `datetime`).

## Design Decisions
- **`last_price` drives `close`; falls back to bid/ask mid** when no trade has printed for the symbol since the bar opened.
- **Per-bar volume from a cumulative day total**: LEVELONE reports `total_volume` cumulatively. We snapshot the cumulative value at first observation in the bar, then `bar.volume = current_cum - snapshot`. Negative deltas are clamped to 0 (defensive against late corrections).
- **Heartbeat-y ticks are silent**: a LEVELONE update with no `last_price`, no bid/ask, and no `total_volume` produces no `("tick", ...)` event. Avoids spamming consumers on quiet channels.
- **Boundary rolls seed `open=high=low=close=prev.close`**: matches the synthetic source's semantics. The next tick will move `close` and expand `high`/`low`.
- **No threads, no time.time()**: callers supply `now`. Lets unit tests advance the clock deterministically.
- **CHART_EQUITY corrections aren't a separate event kind**: callers re-emit them as `("tick", Candle)` and rely on the BarsBuffer's match-by-timestamp to overwrite the prior LEVELONE-synthesized bar. (See `streaming/schwab.py:_dispatch_chart_equity`.)

## Invariants
- `MinuteBarBuilder._bar.start` is always floored to a 1-minute boundary in the source-thread's local timezone (caller chooses by passing a tz-aware `now`).
- `apply_levelone`/`maybe_rollover` emit at most one `("rollover", ...)` per boundary crossed; multiple boundaries crossed in one call yield multiple rollovers.
- Volume monotonically non-decreasing within a bar (clamped at 0 if Schwab's cumulative goes backwards).
- `chart_equity_to_candle` returns `None` for partial messages; malformed present fields may raise during numeric/timestamp coercion.

## Testing
- Unit-tested directly: build a `MinuteBarBuilder`, feed it decoded dicts, advance `now`, assert the emitted event sequence. Suite lives under `tests/unit/streaming/` (covered indirectly via integration smoke tests where unit coverage hasn't been added).
