"""Key-bar pattern detection (RDT-style).

A "key bar" (a.k.a. wide-range bar / igniting bar / elephant bar in the
r/realdaytrading literature) is the bar that "shows institutional
intent": wide range, high volume, and a body that fills most of the
candle. It is the canonical bar #1 of the 1-2-3 / 3-bar-play setup.

Canonical thresholds (locked by user):

* **Range**: bar's True Range > 1.0 × baseline TR.
* **Volume**: bar's RVOL > 1.1 (i.e. > 110% of typical volume).
* **Body**: ``|close - open| / (high - low) > 0.69`` (>69% of the
  high-low span is body).

Direction
---------

* ``close > open`` → +1 (bull key bar).
* ``close < open`` → −1 (bear key bar).
* ``close == open`` → 0; the body would be zero anyway, fails the
  body-ratio rule, so this collapses to "not a key bar".

Baselines
---------

The "TR baseline" and "volume baseline" both adapt to the chart's
interval:

* **Intraday** — same-wall-clock-time baselines:
  * baseline TR  = mean of TR across same-tod bars in the last 20
    regular sessions (delegated to ``ATR(mode="tod", length=20)``).
  * baseline volume = mean volume across same-tod bars in the last
    20 regular sessions, expressed as ratio via
    ``RVOL(mode="time_of_day", length=20, aggregator="mean")``.
* **Daily / weekly / monthly** — plain rolling 20-bar means:
  * baseline TR  = arithmetic mean of TR over the last 20 bars
    (delegated to ``ATR(mode="tod")`` whose tod path falls back to
    the same arithmetic mean on non-intraday data).
  * baseline volume = arithmetic mean of volume over the last 20
    bars; volume RVOL = current_volume / baseline_volume.

Reusing the existing indicators (rather than reimplementing the math)
is a deliberate user-trust property: a chart-overlay ATR ToD or RVOL
ToD will agree exactly with the values the scanner uses to qualify a
key bar.

Asymmetry note
--------------

Range comparison uses **TR** (which captures prior-close gap), but
the body-ratio denominator uses **(high − low)** (no gap). This is
consistent with how traders eyeball bars: visually the body sits
inside the high-low rectangle even though the bar's true range may
exceed that span on a gap.

Output
------

:func:`compute_key_bar_arrays` returns a :class:`KeyBarArrays`:

* ``signed`` — int8 array, one entry per bar:
   ``+1`` bull key bar, ``-1`` bear key bar, ``0`` not a key bar,
   ``-128`` insufficient data (warmup or NaN inputs). Callers should
   compare against the named constants
   :data:`KEY_BAR_BULL`, :data:`KEY_BAR_BEAR`, :data:`KEY_BAR_NONE`,
   :data:`KEY_BAR_UNKNOWN`.
* ``bars_since_bull`` / ``bars_since_bear`` — int64 distance to the
  most-recent bull/bear key bar (``-1`` if none yet).
* ``last_bull_high`` / ``last_bull_low`` / ``last_bear_high`` /
  ``last_bear_low`` — float64; the H/L of the most recent
  bull/bear key bar visible at index ``i``. ``NaN`` if none yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from ..models import Candle


# Sentinel values for ``signed``. Using int8 keeps storage tight; we
# use -128 (the int8 minimum) for "unknown" so it can never collide
# with a real direction.
KEY_BAR_NONE: int = 0
KEY_BAR_BULL: int = 1
KEY_BAR_BEAR: int = -1
KEY_BAR_UNKNOWN: int = -128


# Canonical thresholds.
TR_THRESHOLD: float = 1.0
RVOL_THRESHOLD: float = 1.1
BODY_RATIO_THRESHOLD: float = 0.69
LOOKBACK_BARS_NON_INTRADAY: int = 20


@dataclass(frozen=True)
class KeyBarArrays:
    """Pre-computed per-index arrays used by scanner builtins + render."""

    signed: np.ndarray            # int8
    bars_since_bull: np.ndarray   # int64
    bars_since_bear: np.ndarray   # int64
    last_bull_high: np.ndarray    # float64
    last_bull_low: np.ndarray     # float64
    last_bear_high: np.ndarray    # float64
    last_bear_low: np.ndarray     # float64

    def __len__(self) -> int:
        return int(self.signed.size)


def _empty_result(n: int) -> KeyBarArrays:
    return KeyBarArrays(
        signed=np.full(n, KEY_BAR_UNKNOWN, dtype=np.int8),
        bars_since_bull=np.full(n, -1, dtype=np.int64),
        bars_since_bear=np.full(n, -1, dtype=np.int64),
        last_bull_high=np.full(n, np.nan, dtype=np.float64),
        last_bull_low=np.full(n, np.nan, dtype=np.float64),
        last_bear_high=np.full(n, np.nan, dtype=np.float64),
        last_bear_low=np.full(n, np.nan, dtype=np.float64),
    )


def _baselines_intraday(candles: list[Candle]) -> tuple[np.ndarray, np.ndarray]:
    """Return (atr_tod_array, rvol_tod_array) for an intraday candle list."""
    # Imported lazily to avoid an import cycle at module load.
    from ..indicators.atr import ATR
    from ..indicators.rvol import RVOL
    atr = ATR(mode="tod", length=20).compute(candles)["atr"]
    rvol = RVOL(
        mode="time_of_day", length=20, aggregator="mean",
        session_filter="regular_only",
    ).compute(candles)["rvol"]
    return atr, rvol


def _baselines_non_intraday(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, vols: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Daily/weekly/monthly fallback: plain rolling 20-bar means.

    * baseline TR = mean of TR over the last L bars (inclusive of i).
    * baseline volume = mean of volume over the last L bars
      (inclusive of i); rvol = vol[i] / baseline_volume.
    """
    from ..indicators.wilder import true_range as _true_range
    n = closes.size
    atr_arr = np.full(n, np.nan, dtype=np.float64)
    rvol_arr = np.full(n, np.nan, dtype=np.float64)
    if n < LOOKBACK_BARS_NON_INTRADAY + 1:
        return atr_arr, rvol_arr
    tr = _true_range(highs, lows, closes)
    L = LOOKBACK_BARS_NON_INTRADAY
    # Rolling mean using cumulative sums (vectorized + NaN-safe via
    # explicit per-window mean since tr[0] is NaN).
    for i in range(L, n):
        win_tr = tr[i - L + 1:i + 1]
        m = float(np.nanmean(win_tr)) if np.isfinite(win_tr).any() else float("nan")
        atr_arr[i] = m
        win_v = vols[i - L:i]  # NOT including current bar, mirrors RVOL convention.
        if win_v.size > 0:
            mv = float(np.mean(win_v))
            rvol_arr[i] = vols[i] / mv if mv > 0.0 else float("nan")
    return atr_arr, rvol_arr


def compute_key_bar_arrays(candles: list[Candle]) -> KeyBarArrays:
    """Compute the full per-bar key-bar derivation set.

    Pure function over a candle list. Safe to call from worker threads.
    """
    n = len(candles)
    if n == 0:
        return _empty_result(0)

    # Lazy imports for is_intraday + Bars to stay decoupled. ``true_range``
    # is computed inside :func:`_kb_kernel`, not here.
    from ..indicators.sessions import is_intraday
    from .bars import Bars

    bars_view = Bars.from_candles(candles)
    opens, highs, lows, closes, vols = (
        bars_view.open, bars_view.high, bars_view.low, bars_view.close, bars_view.volume,
    )

    if is_intraday(candles):
        atr_baseline, rvol_arr = _baselines_intraday(candles)
    else:
        atr_baseline, rvol_arr = _baselines_non_intraday(highs, lows, closes, vols)

    return _kb_kernel(opens, highs, lows, closes, atr_baseline, rvol_arr)


# ---------------------------------------------------------------------------
# BarsNp-native entry point
# ---------------------------------------------------------------------------
#
# The scanner stores OHLCV in :class:`tradinglab.scanner.fields.BarsNp` —
# a frozen dataclass of NumPy columns rebuilt at every bar-close. The
# legacy candle-based :func:`compute_key_bar_arrays` forced a full
# ``BarsNp -> List[Candle]`` reconstruction inside the hot scan loop;
# this entry point eliminates that round-trip for the daily / weekly /
# monthly path (which is fully array-native already) and confines the
# candle reconstruction to the intraday branch where the underlying
# ``ATR(mode="tod")`` and the unified ``RVOL(mode="time_of_day")``
# indicators still operate
# on candle lists.


def _is_intraday_np(timestamps: np.ndarray, session: np.ndarray) -> bool:
    """NumPy-native equivalent of :func:`indicators.sessions.is_intraday`.

    Median of consecutive non-gap deltas < 23 hours. We sample at most
    30 deltas to stay cheap on long histories. Returns ``False`` on
    empty input (matches the legacy helper).
    """
    n = int(timestamps.size)
    if n < 2:
        return False
    deltas: list[float] = []
    prev_dt: np.datetime64 | None = None
    for i in range(n):
        sess_i = session[i]
        if sess_i == "gap":
            continue
        cur = timestamps[i]
        if prev_dt is not None:
            dt_s = float((cur - prev_dt).astype("timedelta64[s]").astype(np.int64))
            if dt_s > 0:
                deltas.append(dt_s)
            if len(deltas) >= 30:
                break
        prev_dt = cur
    if not deltas:
        return False
    deltas.sort()
    median = deltas[len(deltas) // 2]
    return median < 23 * 3600


def _bars_np_to_candles(b: Any) -> list[Candle]:
    """Reconstruct a ``List[Candle]`` from a :class:`BarsNp` snapshot.

    Only invoked on the intraday branch of :func:`compute_key_bar_arrays_np`
    because ``ATR(mode="tod")`` and ``RVOL(mode="time_of_day")`` still
    consume candle lists. Lives here (rather than in scanner/fields)
    so the scanner cache can call into a single BarsNp-native API.
    """
    from ..models import Candle as _Candle

    n = int(b.close.size)
    if n == 0:
        return []
    ts = b.timestamps.astype("datetime64[us]").astype(object)
    sess = b.session
    out: list[_Candle] = []
    for i in range(n):
        out.append(_Candle(
            date=ts[i],
            open=float(b.open[i]),
            high=float(b.high[i]),
            low=float(b.low[i]),
            close=float(b.close[i]),
            volume=int(b.volume[i]),
            session=str(sess[i]) if sess[i] is not None else "regular",
        ))
    return out


def _kb_kernel(
    opens: np.ndarray, highs: np.ndarray, lows: np.ndarray,
    closes: np.ndarray, atr_baseline: np.ndarray, rvol_arr: np.ndarray,
) -> KeyBarArrays:
    """Shared per-bar reduction. Takes already-computed baselines."""
    from ..indicators.wilder import true_range as _true_range

    n = closes.size
    tr = _true_range(highs, lows, closes)
    signed = np.full(n, KEY_BAR_UNKNOWN, dtype=np.int8)
    spans = highs - lows
    bodies = np.abs(closes - opens)

    for i in range(n):
        a = atr_baseline[i]
        v = rvol_arr[i]
        s = spans[i]
        b = bodies[i]
        t = tr[i]
        if not (np.isfinite(a) and np.isfinite(v) and np.isfinite(t)):
            signed[i] = KEY_BAR_UNKNOWN
            continue
        if a <= 0.0 or s <= 0.0:
            signed[i] = KEY_BAR_NONE
            continue
        rng_ok = t > TR_THRESHOLD * a
        rvol_ok = v > RVOL_THRESHOLD
        body_ok = (b / s) > BODY_RATIO_THRESHOLD
        if rng_ok and rvol_ok and body_ok:
            if closes[i] > opens[i]:
                signed[i] = KEY_BAR_BULL
            elif closes[i] < opens[i]:
                signed[i] = KEY_BAR_BEAR
            else:
                signed[i] = KEY_BAR_NONE
        else:
            signed[i] = KEY_BAR_NONE

    bars_since_bull = np.full(n, -1, dtype=np.int64)
    bars_since_bear = np.full(n, -1, dtype=np.int64)
    last_bull_high = np.full(n, np.nan, dtype=np.float64)
    last_bull_low  = np.full(n, np.nan, dtype=np.float64)
    last_bear_high = np.full(n, np.nan, dtype=np.float64)
    last_bear_low  = np.full(n, np.nan, dtype=np.float64)

    last_bull_idx = -1
    last_bear_idx = -1
    cur_bull_h = np.nan; cur_bull_l = np.nan
    cur_bear_h = np.nan; cur_bear_l = np.nan
    for i in range(n):
        s = int(signed[i])
        if s == KEY_BAR_BULL:
            last_bull_idx = i
            cur_bull_h = float(highs[i]); cur_bull_l = float(lows[i])
        elif s == KEY_BAR_BEAR:
            last_bear_idx = i
            cur_bear_h = float(highs[i]); cur_bear_l = float(lows[i])
        if last_bull_idx >= 0:
            bars_since_bull[i] = i - last_bull_idx
            last_bull_high[i] = cur_bull_h
            last_bull_low[i]  = cur_bull_l
        if last_bear_idx >= 0:
            bars_since_bear[i] = i - last_bear_idx
            last_bear_high[i] = cur_bear_h
            last_bear_low[i]  = cur_bear_l

    return KeyBarArrays(
        signed=signed,
        bars_since_bull=bars_since_bull,
        bars_since_bear=bars_since_bear,
        last_bull_high=last_bull_high,
        last_bull_low=last_bull_low,
        last_bear_high=last_bear_high,
        last_bear_low=last_bear_low,
    )


def compute_key_bar_arrays_np(b: Any) -> KeyBarArrays:
    """BarsNp-native key-bar derivation (avoids candle round-trip on daily+).

    *b* is duck-typed against :class:`tradinglab.scanner.fields.BarsNp`
    (the type isn't imported here to avoid a scanner -> core -> scanner
    cycle). For non-intraday inputs the entire path is pure NumPy; for
    intraday inputs we reconstruct a candle list locally so the existing
    ``ATR(mode="tod")`` / ``RVOL(mode="time_of_day")`` baselines work
    unchanged.
    """
    n = int(b.close.size)
    if n == 0:
        return _empty_result(0)

    if _is_intraday_np(b.timestamps, b.session):
        candles = _bars_np_to_candles(b)
        atr_baseline, rvol_arr = _baselines_intraday(candles)
    else:
        atr_baseline, rvol_arr = _baselines_non_intraday(
            b.high, b.low, b.close, b.volume.astype(np.float64),
        )

    return _kb_kernel(b.open, b.high, b.low, b.close, atr_baseline, rvol_arr)

