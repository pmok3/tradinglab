# backtest/clock.py — Spec

## Purpose
Monotonic bar-index advancer over the engine's master timeline. The sandbox is multi-ticker but synchronous — every "Next bar" advances all loaded tickers in lockstep. The clock owns the master timeline (an `int64` ndarray of epoch seconds) so there is one authoritative answer to "what time is it now?" regardless of which ticker is focused.

## Public API
- `@dataclass class Clock(timeline: np.ndarray, index: int = -1)`.
- `__len__` — timeline length.
- `is_started: bool` — True once `tick()` has been called at least once.
- `is_exhausted: bool` — True when no further tick will succeed.
- `now_ts: int` — current ts; returns `-1` before the first tick.
- `tick() -> bool` — advance one bar. False if already exhausted.

## Dependencies
- External: `numpy`.

## Design Decisions
- **Starts at `index = -1`** (no bar visible). The first `tick()` lands `index = 0`. This keeps "current bar" and "ticks called" trivially related and makes `is_exhausted` decidable from `index + 1 >= len(timeline)`.
- **Symbol-agnostic**: per-symbol `BarSeries` are aligned to the master timeline at the engine layer (`SandboxEngine._index_by_symbol_at`). The clock itself never sees symbol data.
- **Strict dtype/dim guards** in `__post_init__` — `int64`, 1-D — so a malformed timeline fails loudly at construction rather than producing silent off-by-one errors during tick.

## Invariants
- `is_started == (index >= 0)`.
- `is_exhausted == (index + 1 >= len(timeline))`.
- `tick()` after exhaustion is a no-op returning `False`; it never advances past `len(timeline) - 1`.
- Internal clock and bar timestamps are `int64` epoch seconds (UTC by convention). Display timezone (default US/Eastern for US equities) is applied only at render time. Strategies / engine logic must not assume any particular wall-clock timezone.

## Testing
- `check_f0_backtest_kernel` §B exercises start state, full-timeline traversal, and exhaustion.

