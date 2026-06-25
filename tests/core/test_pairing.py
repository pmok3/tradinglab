"""Tests for ``core.pairing.align_pair`` daily-vs-intraday alignment.

Regression focus: the compare-mode "today gap" bug. A primary ticker's
*synthesized* today daily bar carries the session-open time (e.g. 09:30 ET,
see ``data.today_upsample``), while the compare ticker's today bar is a
provider partial at midnight (its intraday wasn't cached, so no synth). With
exact-timestamp keying these two same-day bars landed in DIFFERENT slots,
producing a gap before today on the primary and a blank "tomorrow" on the
compare. ``align_pair`` now keys daily+ intervals on the calendar date.
Audit ``compare-daily-today-align``.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from tradinglab.core.pairing import align_pair, apply_pair_filter_and_align
from tradinglab.models import Candle

ET = ZoneInfo("America/New_York")


def _daily(day: dt.date, px: float, *, hour: int = 0, minute: int = 0,
           tz: ZoneInfo | None = ET) -> Candle:
    d = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    return Candle(date=d, open=px, high=px + 1, low=px - 1, close=px + 0.5,
                  volume=1000)


def _intraday(day: dt.date, t: dt.time, px: float) -> Candle:
    d = dt.datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=ET)
    return Candle(date=d, open=px, high=px + 0.5, low=px - 0.5, close=px + 0.2,
                  volume=500)


_TODAY = dt.date(2026, 6, 5)
_DAYS = [_TODAY - dt.timedelta(days=k) for k in range(4, 0, -1)]  # 4 prior days


# ---------------------------------------------------------------------------
# The core regression: synth-today (09:30) vs provider-today (midnight).
# ---------------------------------------------------------------------------

def test_daily_today_synth_aligns_with_midnight_provider():
    primary = [_daily(d, 100 + i) for i, d in enumerate(_DAYS)]
    primary.append(_daily(_TODAY, 105.0, hour=9, minute=30))  # synth @ open
    compare = [_daily(d, 200 + i) for i, d in enumerate(_DAYS)]
    compare.append(_daily(_TODAY, 206.0))  # provider partial @ midnight

    out_p, out_c = align_pair(primary, compare, interval="1d")

    # One slot per distinct calendar day — NOT one extra for the time skew.
    assert len(out_p) == len(out_c) == len(_DAYS) + 1
    # No gap anywhere: every day has a real bar on both sides.
    assert not any(c.is_gap for c in out_p)
    assert not any(c.is_gap for c in out_c)
    # Today is the last slot on both sides, both real.
    assert out_p[-1].close == 105.5
    assert out_c[-1].close == 206.5
    assert out_p[-1].date.date() == _TODAY
    assert out_c[-1].date.date() == _TODAY


def test_daily_today_gap_before_fix_signature_without_interval():
    """Back-compat: without ``interval`` the legacy exact-timestamp keying
    is preserved, so the 09:30-vs-midnight skew still splits into two slots.
    (Pins that the daily snap is gated on the interval argument.)"""
    primary = [_daily(d, 100 + i) for i, d in enumerate(_DAYS)]
    primary.append(_daily(_TODAY, 105.0, hour=9, minute=30))
    compare = [_daily(d, 200 + i) for i, d in enumerate(_DAYS)]
    compare.append(_daily(_TODAY, 206.0))

    out_p, out_c = align_pair(primary, compare)  # no interval

    assert len(out_p) == len(_DAYS) + 2  # the spurious extra today slot
    assert any(c.is_gap for c in out_p)
    assert any(c.is_gap for c in out_c)


def test_apply_pair_filter_and_align_threads_interval_1d():
    """End-to-end through the public entry point: 1d removes the skew gap."""
    primary = [_daily(d, 100 + i) for i, d in enumerate(_DAYS)]
    primary.append(_daily(_TODAY, 105.0, hour=9, minute=30))
    compare = [_daily(d, 200 + i) for i, d in enumerate(_DAYS)]
    compare.append(_daily(_TODAY, 206.0))

    out_p, out_c = apply_pair_filter_and_align(primary, compare, "1d", False)

    assert len(out_p) == len(out_c) == len(_DAYS) + 1
    assert not any(c.is_gap for c in out_p)
    assert not any(c.is_gap for c in out_c)


# ---------------------------------------------------------------------------
# Legitimate gaps (a genuinely missing day) must still be preserved.
# ---------------------------------------------------------------------------

def test_daily_missing_day_still_produces_gap():
    # Compare is missing the 2nd prior day (e.g. halted/no print).
    primary = [_daily(d, 100 + i) for i, d in enumerate(_DAYS)]
    compare = [_daily(d, 200 + i) for i, d in enumerate(_DAYS) if d != _DAYS[1]]

    out_p, out_c = align_pair(primary, compare, interval="1d")

    assert len(out_p) == len(out_c) == len(_DAYS)
    # The missing-day slot is a gap on the compare side only.
    gap_idx = [i for i, c in enumerate(out_c) if c.is_gap]
    assert gap_idx == [1]
    assert not out_p[1].is_gap
    # The gap borrows the real (primary) bar's date so the slot is keyed.
    assert out_c[1].date.date() == _DAYS[1]


def test_daily_aligned_real_bars_preserve_identity():
    primary = [_daily(d, 100 + i) for i, d in enumerate(_DAYS)]
    compare = [_daily(d, 200 + i) for i, d in enumerate(_DAYS)]

    out_p, out_c = align_pair(primary, compare, interval="1d")

    # Real bars are the SAME objects (streaming relies on this).
    for src, out in ((primary, out_p), (compare, out_c)):
        reals = [c for c in out if not c.is_gap]
        for orig, got in zip(src, reals, strict=False):
            assert got is orig


# ---------------------------------------------------------------------------
# Compare-mode "today drop" regression: a lagging side must NOT clip the
# other side's trailing bars (audit ``compare-today-drilldown-clip``).
# ---------------------------------------------------------------------------


def _intraday_session(day: dt.date, *, n: int = 6, px0: float = 100.0,
                      start: dt.time = dt.time(9, 30)) -> list[Candle]:
    out: list[Candle] = []
    t = dt.datetime(day.year, day.month, day.day, start.hour, start.minute, tzinfo=ET)
    px = px0
    for _ in range(n):
        out.append(Candle(date=t, open=px, high=px + 0.5, low=px - 0.5,
                          close=px + 0.2, volume=500))
        t += dt.timedelta(minutes=5)
        px += 0.1
    return out


def test_intraday_keeps_primary_today_when_compare_lags_a_day():
    """The reported bug: drilled into TODAY on 5m, toggle compare whose
    intraday cache still ends YESTERDAY. The aligned primary MUST keep
    today's bars — otherwise the index-based drill-down xlim points past the
    end of the now-shorter primary list and every candle vanishes. The
    compare side gets gap placeholders for today."""
    yest = _TODAY - dt.timedelta(days=1)
    primary = _intraday_session(yest, px0=100.0) + _intraday_session(_TODAY, px0=110.0)
    compare = _intraday_session(yest, px0=200.0)  # stale: no today bars

    out_p, out_c = align_pair(primary, compare, interval="5m")

    assert len(out_p) == len(out_c)
    # Every primary real bar survives (none clipped by the lagging compare).
    p_reals = [c for c in out_p if not c.is_gap]
    assert len(p_reals) == len(primary)
    today_p = [c for c in out_p if not c.is_gap and c.date.date() == _TODAY]
    assert len(today_p) == 6, "primary's today bars must survive alignment"
    # Today's slots are gaps on the (lagging) compare side.
    today_c = [c for c in out_c if c.date.date() == _TODAY]
    assert today_c and all(c.is_gap for c in today_c)
    # Real bars keep identity.
    assert today_p[0] is primary[6]


def test_intraday_keeps_compare_when_it_extends_past_primary():
    """Symmetric: a compare that extends FURTHER than primary keeps its
    trailing bars too (primary gets the gaps)."""
    yest = _TODAY - dt.timedelta(days=1)
    primary = _intraday_session(yest, px0=100.0)  # primary stale
    compare = _intraday_session(yest, px0=200.0) + _intraday_session(_TODAY, px0=210.0)

    out_p, out_c = align_pair(primary, compare, interval="5m")
    c_reals = [c for c in out_c if not c.is_gap]
    assert len(c_reals) == len(compare)
    today_c = [c for c in out_c if not c.is_gap and c.date.date() == _TODAY]
    assert len(today_c) == 6


def test_daily_keeps_primary_today_when_compare_lacks_today():
    """Daily analogue: the compare daily series lacks today; the primary's
    today daily bar must not be clipped (a gap fills the compare slot)."""
    primary = [_daily(d, 100 + i) for i, d in enumerate(_DAYS)]
    primary.append(_daily(_TODAY, 105.0, hour=9, minute=30))
    compare = [_daily(d, 200 + i) for i, d in enumerate(_DAYS)]  # no today

    out_p, out_c = align_pair(primary, compare, interval="1d")
    assert out_p[-1].date.date() == _TODAY
    assert not out_p[-1].is_gap
    assert out_c[-1].is_gap  # compare has no today bar → gap


def test_low_end_still_intersects_to_avoid_long_leading_gaps():
    """The LOW end keeps the intersection (lo_day = max of the two starts) so
    a short-history side doesn't force a long leading gap-run on the other —
    only the trailing (today) clip was relaxed."""
    primary = [_daily(d, 100 + i) for i, d in enumerate(_DAYS)]       # 4 days
    compare = [_daily(d, 200 + i) for i, d in enumerate(_DAYS[2:])]   # last 2 days
    out_p, out_c = align_pair(primary, compare, interval="1d")
    assert len(out_p) == len(out_c) == 2
    assert not any(c.is_gap for c in out_p + out_c)


def test_no_shared_day_returns_unaligned():
    """Overlap guard preserved: two series with no common calendar day are
    left unaligned (no giant all-gap lists)."""
    a = [_daily(_DAYS[0], 100.0)]
    b = [_daily(_TODAY + dt.timedelta(days=10), 200.0)]
    out_p, out_c = align_pair(a, b, interval="1d")
    assert len(out_p) == 1 and len(out_c) == 1
    assert not out_p[0].is_gap and not out_c[0].is_gap


def test_intraday_keys_on_exact_timestamp():
    # Same calendar day, different minute stamps must NOT collapse.
    primary = [_intraday(_TODAY, dt.time(9, 30), 100.0),
               _intraday(_TODAY, dt.time(9, 35), 100.5)]
    compare = [_intraday(_TODAY, dt.time(9, 30), 200.0),
               _intraday(_TODAY, dt.time(9, 40), 200.5)]  # 9:40, not 9:35

    out_p, out_c = align_pair(primary, compare, interval="5m")

    # 9:30 aligns; 9:35 (primary-only) and 9:40 (compare-only) are separate
    # slots with gaps — exactly the legacy behaviour.
    assert len(out_p) == len(out_c) == 3
    assert sum(c.is_gap for c in out_p) == 1
    assert sum(c.is_gap for c in out_c) == 1


def test_weekly_keys_on_calendar_date():
    """1wk is daily-class: keyed on the date so same-week anchors align."""
    mondays = [dt.date(2026, 5, 4), dt.date(2026, 5, 11), dt.date(2026, 5, 18)]
    primary = [_daily(d, 100 + i) for i, d in enumerate(mondays)]
    compare = [_daily(d, 200 + i) for i, d in enumerate(mondays)]

    out_p, out_c = align_pair(primary, compare, interval="1wk")

    assert len(out_p) == len(out_c) == 3
    assert not any(c.is_gap for c in out_p + out_c)
