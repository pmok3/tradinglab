"""Canonical Candle-builder fixtures for the test suite.

This module is the single source of truth for synthetic candle generation
in tests. It replaces ~20 ad-hoc ``_make_candles`` / ``_ramp`` / ``_mk_candles``
/ ``_fake_candles`` helpers scattered across ``tests/`` that differ subtly in
seed datetime, tz-awareness, interval, and ramp/random behavior.

Design choices (with rationale):

* **Default seed is Monday 2024-06-03 09:30 ET, tz-aware.** Side-steps the
  CLAUDE.md §7.10 landmine where ``datetime(2024, 6, 1, 9, 30)`` (a Saturday,
  tz-naive) silently produced zero fills under ``require_market_open=True``
  strategies and zero bars through ``runner._filter_rth_only``.
* **Continuous OHLC:** ``open == prev_close`` (or ``start_price`` at i=0),
  ``high = max(o, c) + 0.5``, ``low = min(o, c) - 0.5``. No gaps, no spikes.
* **Volume:** ``1000 + i`` — deterministic, monotone, distinguishable per bar.
* **Session:** ``"regular"`` by default (matches ``Candle.session`` default).

Public surface:

* :func:`ramp` — N candles, linear close ramp by ``step``.
* :func:`flat` — N candles all at one price (close == open == price).
* :func:`random_walk` — deterministic-via-seed GBM-ish closes.
* :func:`daily` — N daily-interval candles starting at ``start_date``.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradinglab.models import Candle

ET = ZoneInfo("America/New_York")
RTH_OPEN_ET = (9, 30)
RTH_CLOSE_ET = (16, 0)

#: Default seed datetime — Monday 2024-06-03 09:30 ET (tz-aware).
#: Monday avoids the §7.10 Saturday-seed landmine; 09:30 ET sits at the RTH
#: open so ``runner._filter_rth_only`` keeps every bar from a 5m/15m ramp
#: that fits inside the trading day.
DEFAULT_MONDAY = datetime(2024, 6, 3, *RTH_OPEN_ET, tzinfo=ET)


def _default_start(tz_aware: bool, *, daily_mode: bool = False) -> datetime:
    if daily_mode:
        base = datetime(2024, 6, 3)
    else:
        base = datetime(2024, 6, 3, *RTH_OPEN_ET)
    if tz_aware:
        return base.replace(tzinfo=ET)
    return base


def _build_bar(t: datetime, op: float, cl: float, i: int, session: str) -> Candle:
    hi = max(op, cl) + 0.5
    lo = min(op, cl) - 0.5
    return Candle(date=t, open=op, high=hi, low=lo, close=cl,
                  volume=1000 + i, session=session)


def ramp(
    n: int,
    *,
    start: datetime | None = None,
    interval_min: int = 5,
    start_price: float = 100.0,
    step: float = 0.10,
    tz_aware: bool = True,
    session: str = "regular",
) -> list[Candle]:
    """Build a ramp of ``n`` candles starting at ``start`` with a linear price
    increment of ``step`` per bar.

    Defaults to a Monday 2024-06-03 09:30 ET tz-aware seed so RTH-gated
    strategies (``require_market_open=True``) fire normally. Pass
    ``tz_aware=False`` for tests that explicitly need naive timestamps.
    """
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if start is None:
        start = _default_start(tz_aware)
    delta = timedelta(minutes=interval_min)
    out: list[Candle] = []
    prev_close = start_price
    t = start
    for i in range(n):
        op = prev_close
        cl = start_price + step * (i + 1)
        out.append(_build_bar(t, op, cl, i, session))
        prev_close = cl
        t = t + delta
    return out


def flat(n: int, *, price: float = 100.0, **kw) -> list[Candle]:
    """``n`` flat candles at ``price`` (close == open == price). Pass-through
    kwargs (``start``, ``interval_min``, ``tz_aware``, ``session``) forward
    to :func:`ramp`."""
    return ramp(n, start_price=price, step=0.0, **kw)


def random_walk(
    n: int,
    *,
    seed: int = 0,
    start_price: float = 100.0,
    vol: float = 0.01,
    **kw,
) -> list[Candle]:
    """``n`` candles with closes drawn from a deterministic GBM-ish walk
    (``close[i] = close[i-1] * (1 + N(0, vol))``). Same ``seed`` →
    bit-identical output across calls. Pass-through kwargs forward to
    :func:`ramp` semantics (``start``, ``interval_min``, ``tz_aware``,
    ``session``)."""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    start = kw.pop("start", None)
    interval_min = kw.pop("interval_min", 5)
    tz_aware = kw.pop("tz_aware", True)
    session = kw.pop("session", "regular")
    if kw:
        raise TypeError(f"unexpected kwargs: {sorted(kw)}")
    if start is None:
        start = _default_start(tz_aware)
    rng = random.Random(seed)
    delta = timedelta(minutes=interval_min)
    out: list[Candle] = []
    prev_close = start_price
    t = start
    for i in range(n):
        op = prev_close
        cl = op * (1.0 + rng.gauss(0.0, vol))
        out.append(_build_bar(t, op, cl, i, session))
        prev_close = cl
        t = t + delta
    return out


def daily(
    n: int,
    *,
    start_date: datetime | None = None,
    start_price: float = 100.0,
    step: float = 0.10,
    tz_aware: bool = False,
    session: str = "regular",
) -> list[Candle]:
    """``n`` daily candles starting at ``start_date`` (default Monday
    2024-06-03). One bar per calendar day; price ramps by ``step``. tz-naive
    by default because daily bars are typically date-only in this codebase."""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if start_date is None:
        start_date = _default_start(tz_aware, daily_mode=True)
    out: list[Candle] = []
    prev_close = start_price
    t = start_date
    for i in range(n):
        op = prev_close
        cl = start_price + step * (i + 1)
        out.append(_build_bar(t, op, cl, i, session))
        prev_close = cl
        t = t + timedelta(days=1)
    return out
