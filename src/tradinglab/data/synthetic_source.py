"""Synthetic (offline, deterministic) historical data source.

Used for development, smoke tests, and as the history bootstrap for the
synthetic streaming source (which wants a seed that doesn't collide with
its own in-progress rollover bar).
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from ..constants import (
    classify_session,
    floor_to_interval,
    interval_minutes,
    is_intraday,
)
from ..models import Candle


def fetch_synthetic_data(ticker: str = "AMD", interval: str = "1d") -> list[Candle] | None:
    """Generate a synthetic random-walk OHLCV history for testing.

    For intraday intervals the generator emits bars across the full
    pre-market (04:00 ET) → post-market (20:00 ET) window on each trading
    day, with each bar tagged ``pre`` / ``regular`` / ``post``. Extended-hours
    bars are given a small-volume bias (~15% of RTH) to mimic thinner
    liquidity, which is useful for exercising the extended-hours toggle.

    The seed is derived from the ticker so a given symbol always yields the
    same series across runs — handy when comparing charts.
    """
    rng = random.Random(hash((ticker, interval)) & 0xFFFFFFFF)
    base_price = 50.0 + rng.random() * 450.0  # between ~50 and ~500

    # Generate ~60 days of bars for intraday, or ~500 daily bars otherwise.
    # These sizes mirror what yfinance returns for the same interval.
    if is_intraday(interval):
        step_min = interval_minutes(interval)
        days = 60 if interval != "1m" else 7
        return _gen_intraday(rng, base_price, interval, step_min, days)
    return _gen_daily(rng, base_price, interval, 500)


def fetch_synthetic_stream_bootstrap(
    ticker: str = "AMD", interval: str = "1d",
) -> list[Candle] | None:
    """Seed history for the synthetic-stream source.

    Re-uses :func:`fetch_synthetic_data` for the bulk of the walk, then
    **truncates** any bars whose start timestamp is at or past the
    current interval boundary. That way the stream, which opens a
    fresh in-progress bar at the current boundary, doesn't emit a
    rollover that collides with a pre-existing seeded bar at the same
    timestamp.

    For non-intraday intervals (where streaming is a no-op anyway)
    this falls through to the plain synthetic series.
    """
    candles = fetch_synthetic_data(ticker, interval)
    if candles is None or not is_intraday(interval):
        return candles
    boundary = floor_to_interval(datetime.now(), interval_minutes(interval))
    return [c for c in candles if c.date < boundary]


# ---------------------------------------------------------------------------
# Internal helpers — random-walk primitive + per-interval generators.
# ---------------------------------------------------------------------------

def _step(rng: random.Random, price: float, vol_scale: float) -> tuple:
    """Advance a random walk one step, returning (o,h,l,c,volume,new_price)."""
    # Log-normal step: daily vol ~1.5% → per-bar scaled by vol_scale.
    sigma = 0.015 * vol_scale
    drift = rng.gauss(0, sigma)
    o = price
    c = price * math.exp(drift)
    # High/low around the [o, c] envelope with a small random wick.
    wick = abs(rng.gauss(0, sigma * 0.6)) * price
    h = max(o, c) + wick
    l = min(o, c) - wick
    l = max(l, 0.01)  # never negative
    return o, h, l, c, c


def _gen_intraday(rng, base_price, interval, step_min, days):
    """Generate intraday bars from 04:00→20:00 ET, 5 days per week, ``days`` total."""
    candles: list[Candle] = []
    price = base_price
    # Start ``days`` business days ago at 04:00 ET.
    end = datetime.now().replace(hour=20, minute=0, second=0, microsecond=0)
    start_date = (end - timedelta(days=days)).date()
    day = start_date
    while day <= end.date():
        if day.weekday() >= 5:  # skip weekends
            day += timedelta(days=1)
            continue
        t = datetime.combine(day, datetime.min.time()).replace(hour=4)
        day_end = t.replace(hour=20)
        while t < day_end:
            session = classify_session(t.hour, t.minute)
            # Intraday step ≈ daily_vol / sqrt(bars_per_day); ~78 RTH 5m bars.
            vol_scale = (step_min / 390) ** 0.5
            o, h, l, c, price = _step(rng, price, vol_scale)
            # Pre/post volume is a small fraction of RTH.
            rth_vol = rng.randint(50_000, 500_000)
            volume = rth_vol if session == "regular" else int(rth_vol * 0.15)
            candles.append(Candle(
                date=t, open=o, high=h, low=l, close=c,
                volume=volume, session=session,
            ))
            t += timedelta(minutes=step_min)
        day += timedelta(days=1)
    return candles


def _gen_daily(rng, base_price, interval, count):
    """Generate ``count`` daily/weekly/monthly bars ending today."""
    step = {"1d": timedelta(days=1), "1wk": timedelta(weeks=1),
            "1mo": timedelta(days=30)}.get(interval, timedelta(days=1))
    candles: list[Candle] = []
    price = base_price
    t = datetime.now().replace(hour=16, minute=0, second=0, microsecond=0)
    t -= step * count
    for _ in range(count):
        if interval == "1d" and t.weekday() >= 5:
            t += step
            continue
        o, h, l, c, price = _step(rng, price, vol_scale=1.0)
        candles.append(Candle(
            date=t, open=o, high=h, low=l, close=c,
            volume=rng.randint(1_000_000, 50_000_000), session="regular",
        ))
        t += step
    return candles
