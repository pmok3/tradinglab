from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradinglab.core.timezones import ET
from tradinglab.models import Candle
from tradinglab.streaming.intraday import CHART_INTERVALS, IntradayAdapter


def bar(minute: int, *, volume: int = 10, high: float = 102) -> Candle:
    return Candle(datetime(2026, 9, 8, 9, 30, tzinfo=ET) + timedelta(minutes=minute),
                  100, high, 99, 101, volume)


@pytest.mark.parametrize("interval", sorted(CHART_INTERVALS))
def test_startup_minute_requires_explicit_authoritative_correction(interval):
    adapter = IntradayAdapter(interval)
    assert adapter.apply("rollover", bar(0)) == []
    assert adapter.apply("tick", bar(0, volume=50)) == []
    assert not adapter.ready
    result = adapter.apply("closed", bar(0, volume=100))
    assert result[-1][1].volume == 100
    assert adapter.ready
    adapter.apply("tick", bar(1, volume=20))
    correction = adapter.apply("closed", bar(0, volume=30, high=101))
    assert correction[-1][1].volume == 50
    assert correction[-1][1].high == 102


def test_mid_bucket_partial_keeps_history_until_next_safe_bucket():
    adapter = IntradayAdapter("5m", history=[bar(0, volume=900)])
    assert adapter.apply("tick", bar(3)) == []
    assert adapter.apply("closed", bar(3)) == []
    assert adapter.apply("closed", bar(4)) == []
    result = adapter.apply("rollover", bar(5))
    assert len(result) == 1
    assert result[0][1].date == bar(5).date
    assert not adapter.ready
    assert adapter.needs_reconcile
    assert adapter.history_refreshed(history=[bar(0, volume=1200)], fresh=[bar(0, volume=1200)])
    assert adapter.ready


def test_cached_seed_preserves_prefix_and_protects_startup_minute():
    adapter = IntradayAdapter("5m", history=[bar(0)], seed=[bar(i) for i in range(4)])
    assert adapter.apply("tick", bar(3)) == []
    assert adapter.apply("closed", bar(3)) == []
    result = adapter.apply("closed", bar(4))
    assert result[-1][1].volume == 50
    assert adapter.ready


def test_gap_requires_real_reconciliation():
    adapter = IntradayAdapter("5m")
    adapter.apply("closed", bar(0))
    assert adapter.ready
    assert adapter.apply("tick", bar(2)) == []
    assert adapter.needs_reconcile
    assert not adapter.ready
    assert adapter.apply("tick", bar(3)) == []
    adapter.reconcile(history=[bar(0)], seed=[bar(0), bar(1), bar(2)])
    assert not adapter.needs_reconcile
    assert adapter.apply("tick", bar(2)) == []
    assert adapter.apply("closed", bar(2)) == []
    assert adapter.apply("closed", bar(3)) == []
    assert adapter.apply("closed", bar(4))


def test_evicted_correction_requires_reconciliation():
    adapter = IntradayAdapter("5m")
    for minute in range(12):
        adapter.apply("closed", bar(minute))
    assert adapter.resampler.retained_minute_count == 7
    assert adapter.apply("closed", bar(0)) == []
    assert adapter.needs_reconcile


def test_previous_bucket_correction_reduces_high_and_volume():
    adapter = IntradayAdapter("5m")
    for minute in range(6):
        adapter.apply("closed", bar(minute, high=200 if minute == 2 else 102))
    for _ in range(2):
        result = adapter.apply("closed", bar(2, volume=1, high=101))
        assert result[0][0] == "closed"
        assert result[0][1].volume == 41
        assert result[0][1].high == 102


def test_misaligned_history_is_never_overwritten():
    adapter = IntradayAdapter("1h", history=[bar(30)])
    assert adapter.needs_reconcile
    assert adapter.apply("closed", bar(30)) == []


def test_poll_history_update_preserves_minutes_but_protects_new_opaque_bucket():
    adapter = IntradayAdapter("5m")
    adapter.apply("closed", bar(0))
    assert adapter.ready
    adapter.observe_history([bar(0, volume=999)])
    assert not adapter.ready
    for minute in range(1, 4):
        assert adapter.apply("closed", bar(minute)) == []
    assert adapter.apply("closed", bar(4))[-1][1].volume == 50


def test_abandoned_bucket_debt_requires_that_bar_in_fresh_history():
    adapter = IntradayAdapter("5m", history=[bar(0, volume=900)])
    adapter.apply("tick", bar(3))
    adapter.apply("closed", bar(3))
    adapter.apply("closed", bar(4))
    revision = adapter.reconcile_revision
    assert adapter.apply("rollover", bar(5))
    assert adapter.reconcile_revision > revision
    assert not adapter.ready
    for minute in range(5, 10):
        adapter.apply("closed", bar(minute))
    assert not adapter.ready, "A fully covered new bucket does not repay earlier history debt"
    assert not adapter.history_refreshed(
        history=[bar(0, volume=900), bar(5, volume=50)], fresh=[bar(5, volume=50)])
    assert adapter.needs_reconcile
    assert adapter.history_refreshed(
        history=[bar(0, volume=1200), bar(5, volume=50)],
        fresh=[bar(0, volume=1200), bar(5, volume=50)])
    assert adapter.ready
    assert adapter.resampler.retained_minute_count == 7
