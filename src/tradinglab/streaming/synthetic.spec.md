# streaming/synthetic.py — Spec

> ⚠ `SyntheticStreamSource` is a deterministic offline simulator (seeded log-normal random walk). NOT a real feed. Never use for live trading or signals against real instruments. For smoke tests, UI dev, sandbox replay only.

## Purpose
Deterministic offline streaming source. One daemon thread per subscription advances a log-normal random walk on the in-progress bar's close; emits ticks at `tick_period` (default 0.5s); rolls over when wall-clock crosses an interval boundary.

## Public API
- `class SyntheticStreamSource(tick_period: float = 0.5)` — implements `StreamSource`.
  - `subscribe(ticker, interval, on_event) -> unsubscribe`. No-op unsubscribe for non-intraday intervals (daily+ doesn't meaningfully stream). Otherwise spawns a daemon thread keyed by a `threading.Event`.

## Dependencies
Internal: `..constants.classify_session/floor_to_interval/interval_minutes/is_intraday`, `..models.Candle`, `.base.StreamCallback`. External: `math`, `random`, `threading`, `datetime`.

## Design Decisions
- **Daemon thread** (`daemon=True`): dies on app exit; `_on_close` also calls unsubscribes for clean teardown.
- **`threading.Event` + `event.wait(tick_period)`** rather than `time.sleep`: wakes immediately on unsubscribe.
- **Seeded RNG**: `hash((ticker, interval, "stream")) & 0xFFFFFFFF`. Different from the history seed (includes the literal `"stream"`) so the live walk doesn't mirror history at the seam.
- **Initial "rollover" event** seeds consumer with an in-progress bar at `floor_to_interval(now, step_min)`. Subsequent ticks mutate it.
- **Rollover detection**: emit rollover whenever `new_start > bar_start`. Previous bar implicitly sealed; no explicit "seal" event.
- **Open continuity across rollover**: `open_px = close_px` from prior bar — simulates a gapless open.
- **Per-tick sigma ≈ 0.15%** (`sigma = 0.0015`): small enough that a 2 Hz tick stream produces a plausible-looking in-progress bar over ~5m.
- **Price floor `0.01`** on close update.
- **High/low envelope** via `max(high_px, close_px)` / `min(low_px, close_px)`.
- **Volume accumulates per tick** (`rng.randint(500, 5000)`), not jump at rollover.
- **`nonlocal price`**: walk's price persists across rollovers within a subscription so continuity is preserved.

## Invariants
- Non-intraday subscribe: immediate return with no-op unsub, no thread spawned.
- `unsubscribe()` sets the stop event; thread exits within one `tick_period`.
- Each emitted Candle has correct `session` via `classify_session(start.hour, start.minute)`.
- Rollover events fire exactly at interval boundaries.
- First emitted event is always `"rollover"`, not `"tick"`.

## Algorithm
```
subscribe(ticker, interval, on_event):
    if not is_intraday(interval): return no-op unsub
    stop = Event()
    thread _run():
        bar_start = floor(now, step_min); open=high=low=close=price; vol=0
        emit ("rollover", Candle(bar_start, ..., classify_session(...)))
        while not stop:
            stop.wait(tick_period)
            if stop: break
            new_start = floor(now, step_min)
            if new_start > bar_start:
                bar_start = new_start
                open = close   # continuity
                high = low = close
                vol = 0
                emit ("rollover", Candle(...))
                continue
            close *= exp(gauss(0, 0.0015))
            close = max(close, 0.01)
            high = max(high, close); low = min(low, close)
            vol += rng.randint(500, 5000)
            emit ("tick", Candle(...))
    thread.start()
    return lambda: stop.set()
```

## Known limitations
- Not a real feed.
- Intraday-only (1m–1h). Daily/weekly/monthly return a no-op unsub.
- No simulated earnings gaps, no weekend handling (Saturday intraday still emits).
