"""Deterministic synthetic event generator for smoke tests.

Mirrors :mod:`tradinglab.data.synthetic_source` in posture: the
output is a pure function of ``ticker``, never hits the network, and
seeds off ``hash((ticker, "events")) & 0xFFFFFFFF`` so two test runs
produce byte-identical bundles.

The generator emits:

* Quarterly earnings every ~91 calendar days from a ticker-seeded
  start date in 2018. BMO/AMC alternation is deterministic. Past
  rows have finite EPS estimate + finite actual; future rows have
  finite estimate + NaN actual.
* Quarterly cash dividends offset ~45 days from earnings prints.
* One special dividend at a deterministic offset per ticker (Q1 2022).
* One forward 2:1 stock split at a deterministic offset per ticker
  (mid-2021) if the ticker hash selects for it.
"""

from __future__ import annotations

import datetime as _dt
import math
import random

from .base import DividendRecord, EarningsRecord, EventBundle

_EPOCH = _dt.datetime(1970, 1, 1)


def _midnight_ms(d: _dt.date) -> int:
    return int((_dt.datetime(d.year, d.month, d.day) - _EPOCH).total_seconds() * 1000)


def fetch_synthetic_events(ticker: str) -> EventBundle | None:
    """Return a deterministic :class:`EventBundle` for ``ticker``.

    Never returns ``None`` in the synthetic generator — by contract
    every ticker has a fabricated schedule. Real providers may return
    ``None`` for delisted / unsupported tickers; consumers should
    tolerate that.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return None
    seed = hash((sym, "events")) & 0xFFFFFFFF
    rng = random.Random(seed)

    earnings = []
    dividends = []

    # Start at a deterministic date in 2018, BMO/AMC alternating.
    base = _dt.date(2018, 1, 1) + _dt.timedelta(days=rng.randrange(60))
    today_ms = int((_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - _EPOCH).total_seconds() * 1000)

    # 8 years of quarterly earnings (≈32 prints). Past prints have
    # finite actuals; the most recent ~4 are future (NaN actuals).
    eps_walk = 1.00
    for i in range(40):
        when = "BMO" if (i % 2 == 0) else "AMC"
        d = base + _dt.timedelta(days=int(91 * i))
        ts = _midnight_ms(d)
        eps_estimate = eps_walk
        if ts > today_ms:
            eps_actual = math.nan
            rev_actual = math.nan
        else:
            # Past row: actual ≈ estimate ± small noise.
            eps_actual = eps_estimate * (1.0 + rng.uniform(-0.08, 0.08))
            rev_actual = (eps_estimate * 1e9) * (1.0 + rng.uniform(-0.04, 0.04))
        earnings.append(EarningsRecord(
            ts=ts,
            symbol=sym,
            when=when,
            eps_estimate=eps_estimate,
            eps_actual=eps_actual,
            revenue_estimate=eps_estimate * 1e9,
            revenue_actual=rev_actual,
            source="synthetic",
        ))
        # Random walk on estimate so the series isn't flat.
        eps_walk *= 1.0 + rng.uniform(-0.02, 0.03)

    # Quarterly cash dividends, offset ~45 days from each earnings print.
    div_amount = 0.10 + rng.uniform(0.0, 0.30)
    for i in range(40):
        d = base + _dt.timedelta(days=int(91 * i) + 45)
        ex_ts = _midnight_ms(d)
        dividends.append(DividendRecord(
            ex_ts=ex_ts,
            symbol=sym,
            amount=round(div_amount, 4),
            kind="cash",
            pay_ts=_midnight_ms(d + _dt.timedelta(days=14)),
            declared_ts=_midnight_ms(d - _dt.timedelta(days=30)),
            source="synthetic",
        ))

    # One special dividend in Q1 2022.
    special_day = _dt.date(2022, 2, 14) + _dt.timedelta(days=rng.randrange(14))
    dividends.append(DividendRecord(
        ex_ts=_midnight_ms(special_day),
        symbol=sym,
        amount=round(div_amount * 5, 4),
        kind="special",
        pay_ts=_midnight_ms(special_day + _dt.timedelta(days=14)),
        declared_ts=_midnight_ms(special_day - _dt.timedelta(days=30)),
        source="synthetic",
    ))

    # ~30% of tickers get a 2:1 forward split mid-2021.
    if (seed % 10) < 3:
        split_day = _dt.date(2021, 7, 1) + _dt.timedelta(days=rng.randrange(60))
        dividends.append(DividendRecord(
            ex_ts=_midnight_ms(split_day),
            symbol=sym,
            amount=math.nan,
            kind="stock_split",
            ratio_num=2,
            ratio_den=1,
            source="synthetic",
        ))

    return EventBundle(
        symbol=sym,
        earnings=earnings,
        dividends=dividends,
        fetched_at=today_ms,
    )


__all__ = ("fetch_synthetic_events",)
