"""Audit ``source-data-range-readout`` — the load-complete readout surfaces
the loaded series' date range so a provider returning stale / incomplete data
is visible.

Motivating bug: switching the data source on a 1d chart made the view land on
old data (a provider returning history that does not reach recent dates). The
time-preserve remap correctly falls back to the new data's right edge, but the
user had no signal WHY the chart "jumped" to an old year. ``_series_date_span``
feeds the enriched status line (``TER 1d: N bars (first → last)``).

These tests pin the pure ``_series_date_span`` helper (including the
``stale_days`` field it returns for callers).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from tradinglab.app import ChartApp
from tradinglab.models import Candle


def _c(dt, *, gap=False):
    if gap:
        return Candle.gap(dt)
    return Candle(date=dt, open=1.0, high=2.0, low=0.5, close=1.5,
                  volume=100, session="regular")


def test_span_none_for_empty_or_all_gap():
    assert ChartApp._series_date_span([]) is None
    base = datetime(2020, 1, 1)
    assert ChartApp._series_date_span(
        [_c(base, gap=True), _c(base + timedelta(days=1), gap=True)]) is None


def test_span_reports_first_last_and_recent_is_not_stale():
    now = datetime.now()
    candles = [_c(now - timedelta(days=3)), _c(now - timedelta(days=1))]
    span = ChartApp._series_date_span(candles)
    assert span is not None
    first_d, last_d, stale_days = span
    assert first_d == (now - timedelta(days=3)).date().isoformat()
    assert last_d == (now - timedelta(days=1)).date().isoformat()
    # Newest bar ~1 day old → not grossly stale.
    assert 0 <= stale_days <= 2


def test_span_flags_years_stale_data():
    # A provider returning data ending years ago (the 1d source-switch
    # symptom) yields a large stale_days the caller can warn on.
    old = datetime(2022, 9, 15)
    candles = [_c(datetime(2011, 6, 28)), _c(old)]
    span = ChartApp._series_date_span(candles)
    assert span is not None
    first_d, last_d, stale_days = span
    assert first_d == "2011-06-28"
    assert last_d == "2022-09-15"
    # Well beyond the 14-day daily gross-staleness threshold.
    assert stale_days > 365


def test_span_ignores_trailing_gap_bars():
    now = datetime.now()
    candles = [
        _c(now - timedelta(days=5)),
        _c(now - timedelta(days=2)),
        _c(now - timedelta(days=1), gap=True),  # trailing gap ignored
    ]
    span = ChartApp._series_date_span(candles)
    assert span is not None
    _first, last_d, _stale = span
    # Last REAL bar is the 2-days-ago one, not the trailing gap.
    assert last_d == (now - timedelta(days=2)).date().isoformat()
