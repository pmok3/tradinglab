"""Gap-aware staleness in ``DataController.is_stale`` (1d only).

A dropped NaN-OHLC poison bar leaves a hole between two present daily bars
(e.g. ``Mon Jun 8`` then ``Wed Jun 10`` with ``Tue Jun 9`` missing). The
last-bar age check can't see that hole — the series looks "fresh" while a
weekday is silently absent. ``is_stale`` now flags such a series stale ONCE
per unique gap per controller session so a single re-fetch + merge can fill
it, without looping on genuine market holidays.

See ``data/controller.spec.md`` and the disk_cache poison-bar landmine.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tradinglab.data.controller import DataController
from tradinglab.models import Candle


def _bar(y: int, m: int, d: int, close: float = 10.0) -> Candle:
    return Candle(
        date=datetime(y, m, d, tzinfo=timezone.utc),
        open=close, high=close, low=close, close=close,
        volume=100, session="regular",
    )


# Anchor "now" late on Wed 2026-06-10 so a last bar dated 2026-06-10 is NOT
# age-stale (only the gap branch can flag it). June 2026: 8=Mon … 10=Wed.
_NOW = datetime(2026, 6, 10, 20, tzinfo=timezone.utc).timestamp()


def test_interior_weekday_gap_flags_stale_once() -> None:
    ctrl = DataController()
    # Fri Jun 5, Mon Jun 8, Wed Jun 10 — Tue Jun 9 is missing (a hole).
    bars = [_bar(2026, 6, 5), _bar(2026, 6, 8), _bar(2026, 6, 10)]
    assert ctrl.is_stale(bars, "1d", now_s=_NOW) is True   # gap → stale
    assert ctrl.is_stale(bars, "1d", now_s=_NOW) is False  # one-shot: no loop


def test_consecutive_days_not_stale() -> None:
    ctrl = DataController()
    bars = [_bar(2026, 6, 8), _bar(2026, 6, 9), _bar(2026, 6, 10)]
    assert ctrl.is_stale(bars, "1d", now_s=_NOW) is False


def test_weekend_is_not_a_gap() -> None:
    ctrl = DataController()
    # Fri → Mon spans only Sat/Sun: no weekday missing.
    bars = [_bar(2026, 6, 5), _bar(2026, 6, 8)]
    now = datetime(2026, 6, 8, 20, tzinfo=timezone.utc).timestamp()
    assert ctrl.is_stale(bars, "1d", now_s=now) is False


def test_filled_gap_is_not_stale() -> None:
    ctrl = DataController()
    gapped = [_bar(2026, 6, 8), _bar(2026, 6, 10)]      # Tue missing
    assert ctrl.is_stale(gapped, "1d", now_s=_NOW) is True
    filled = [_bar(2026, 6, 8), _bar(2026, 6, 9), _bar(2026, 6, 10)]
    assert ctrl.is_stale(filled, "1d", now_s=_NOW) is False


def test_gap_check_is_daily_only() -> None:
    ctrl = DataController()
    # Same hole shape on a weekly series must NOT trigger the 1d gap branch.
    weekly = [_bar(2026, 6, 8), _bar(2026, 6, 10)]
    assert ctrl.is_stale(weekly, "1wk", now_s=_NOW) is False


def test_age_stale_still_wins_for_old_tail() -> None:
    ctrl = DataController()
    # Last bar is weeks old → age-stale regardless of gaps.
    old = [_bar(2026, 5, 1), _bar(2026, 5, 4)]
    assert ctrl.is_stale(old, "1d", now_s=_NOW) is True
