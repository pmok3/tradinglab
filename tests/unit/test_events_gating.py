"""Events gating tests (substitute for smoke b62 / b67 at unit level).

Covers:
* strictly-less-than-or-equal past gating
* forward-window cap
* blind-mode redaction (forward earnings records omitted)
* badge always emitted regardless of blind
* dividend forward redaction
"""
from __future__ import annotations

import math

from tradinglab.events.base import (
    DividendRecord,
    EarningsRecord,
    EventBundle,
)
from tradinglab.events.gating import events_visible_for

MS_PER_DAY = 86_400_000


def _bundle() -> EventBundle:
    return EventBundle(
        symbol="X",
        earnings=[
            EarningsRecord(ts=10 * MS_PER_DAY, symbol="X", when="AMC",
                           eps_estimate=1.0, eps_actual=1.1),
            EarningsRecord(ts=100 * MS_PER_DAY, symbol="X", when="BMO",
                           eps_estimate=2.0),
        ],
        dividends=[
            DividendRecord(ex_ts=20 * MS_PER_DAY, symbol="X",
                           amount=0.25, kind="cash"),
            DividendRecord(ex_ts=110 * MS_PER_DAY, symbol="X",
                           amount=0.30, kind="cash"),
        ],
    )


def test_past_earnings_visible_after_clock_passes():
    v = events_visible_for(_bundle(), 50 * MS_PER_DAY, blind=False)
    assert len(v.past_earnings) == 1
    assert v.past_earnings[0].ts == 10 * MS_PER_DAY


def test_forward_window_cap_excludes_distant_earnings():
    # Window default = 30 days; print at 100 days is too far.
    v = events_visible_for(_bundle(), 50 * MS_PER_DAY, blind=False,
                           forward_window_days=30)
    assert v.forward_earnings == []
    assert v.forward_badges == []


def test_forward_earnings_visible_within_window_non_blind():
    v = events_visible_for(_bundle(), 80 * MS_PER_DAY, blind=False,
                           forward_window_days=30)
    assert len(v.forward_earnings) == 1
    assert v.forward_earnings[0].ts == 100 * MS_PER_DAY
    # Actual is NaN'd defensively even when provider didn't include it.
    assert math.isnan(v.forward_earnings[0].eps_actual)
    assert len(v.forward_badges) == 1


def test_blind_mode_omits_forward_earnings_records_keeps_badge():
    v = events_visible_for(_bundle(), 80 * MS_PER_DAY, blind=True,
                           forward_window_days=30)
    assert v.forward_earnings == []
    assert len(v.forward_badges) == 1
    assert v.forward_badges[0].when == "BMO"


def test_blind_mode_omits_forward_dividends():
    v = events_visible_for(_bundle(), 95 * MS_PER_DAY, blind=True,
                           forward_window_days=30)
    # cash div at 110 days is within window but blind redacts it.
    assert v.forward_dividends == []


def test_past_dividends_visible_after_ex_date():
    v = events_visible_for(_bundle(), 25 * MS_PER_DAY, blind=False)
    assert len(v.past_dividends) == 1
    assert v.past_dividends[0].ex_ts == 20 * MS_PER_DAY


def test_none_bundle_returns_empty_view():
    v = events_visible_for(None, 0, blind=False)  # type: ignore[arg-type]
    assert v.past_earnings == []
    assert v.past_dividends == []
    assert v.forward_earnings == []
    assert v.forward_dividends == []
    assert v.forward_badges == []
