# `indicators/sessions.py`

Shared helpers for session-aware indicators (VWAP, Anchored VWAP,
Relative Volume). One place for DST / half-days / missing-bars / gap
edge cases.

## Public API

```python
from tradinglab.indicators.sessions import (
    # Candle-list API (list-of-Candle inputs)
    session_groups, tod_key, is_intraday,
    session_filter_predicate, TodKey,
    # NumPy / BarsNp API (scanner + vectorized indicators)
    session_groups_np, tod_key_np, is_intraday_np,
    session_filter_mask_np,
)
```

### NumPy / `BarsNp` API

`*_np` variants take a `BarsNp` snapshot (scanner-native columnar
form) and return NumPy arrays. Semantics match the list-based helpers
so the two surfaces never disagree.

- `tod_key_np(bars) -> np.ndarray[int32]` — per-bar `hour*60 + minute`
  in exchange-local wall clock. NaN-safe; out-of-range timestamps
  fall back to `-1`.
- `session_groups_np(bars, *, regular_only=True) -> List[np.ndarray]`
  — per-day index groupings of `bars`. Each entry is an `int64`
  index array.
- `is_intraday_np(bars) -> bool` — median-delta heuristic over the
  first ~30 non-gap timestamps; `< 23h` ⇒ intraday.
- `session_filter_mask_np(bars, filter_mode) -> np.ndarray[bool]` —
  per-bar admission mask for `"regular_only"` /
  `"regular_plus_premarket"` / `"extended"`. Gap rows always rejected.

### `session_groups(candles, *, regular_only=True) -> List[List[int]]`

Group candle indices by calendar trading day, in original order.

* `regular_only=True` (default) skips bars whose `session` is not
  `"regular"` and any bar with `is_gap=True`. Skipped bars do NOT
  start a new session — only the calendar date boundary does.
* Indices into the original `candles` sequence are preserved.

### `tod_key(c) -> Optional[TodKey]`

`(hour, minute)` for a candle's exchange-local wall clock. Used to
compare bars across sessions by time-of-day rather than positional
ordinal (correct under half-day sessions, missing bars, DST).
Returns `None` for unparsable `date`.

### `is_intraday(candles) -> bool`

Median-delta heuristic over the first ~30 non-gap candles. `True`
when median spacing `< 23h`.

### `session_filter_predicate(filter_mode) -> Callable[[Candle], bool]`

Predicate that admits:

* `"regular_only"` — only `session == "regular"`
* `"regular_plus_premarket"` — regular OR `"pre"`
* `"extended"` — regular OR pre OR post

Gap fillers (`is_gap=True`) always rejected. Unknown filter values
fall back to `"regular_only"`.

## Timezone convention

Candle `date` fields are assumed to already be in exchange-local
wall-clock time (US/Eastern for the equities the app supports).
Timezone-aware datetimes are accepted; tzinfo is irrelevant once the
helpers only inspect `hour`/`minute`.
