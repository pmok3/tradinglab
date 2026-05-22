"""Tests for chandelier exit-rule functions in :mod:`exits.spec`."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradinglab.exits.model import ExitTrigger, TriggerKind
from tradinglab.exits.spec import (
    Bar,
    TriggerState,
    evaluate_chandelier_stop,
    freeze_chandelier_params,
    update_chandelier_state,
)
from tradinglab.positions.model import Position


def _long(entry: float = 100.0, qty: float = 100.0) -> Position:
    return Position(
        id="P1", symbol="X", side="long",
        qty_initial=qty, qty_open=qty, avg_entry_price=entry,
        entry_time=datetime(2024, 1, 2, 9, 30), source="manual",
    )


def _short(entry: float = 100.0, qty: float = 100.0) -> Position:
    return Position(
        id="P2", symbol="X", side="short",
        qty_initial=qty, qty_open=qty, avg_entry_price=entry,
        entry_time=datetime(2024, 1, 2, 9, 30), source="manual",
    )


def _trig(**overrides) -> ExitTrigger:
    kw = dict(
        kind=TriggerKind.CHANDELIER,
        chandelier_lookback=5,
        chandelier_atr_period=3,
        chandelier_multiplier=2.0,
        chandelier_ma_type="RMA",
    )
    kw.update(overrides)
    return ExitTrigger(**kw)


def _bar(o: float, h: float, l: float, c: float, i: int = 0) -> Bar:
    return Bar(
        open=o, high=h, low=l, close=c,
        date=datetime(2024, 1, 2, 9, 30) + timedelta(minutes=i),
    )


# ---------------------------------------------------------------------------
# freeze_chandelier_params
# ---------------------------------------------------------------------------


def test_freeze_chandelier_params_uppercases_ma_type() -> None:
    t = _trig(chandelier_ma_type="ema")
    p = freeze_chandelier_params(t)
    assert p == {"lookback": 5, "atr_period": 3, "multiplier": 2.0, "ma_type": "EMA"}


# ---------------------------------------------------------------------------
# update_chandelier_state
# ---------------------------------------------------------------------------


def test_activation_seeds_window_and_freezes_params() -> None:
    s = TriggerState()
    t = _trig()
    p = _long()
    b = _bar(100.0, 101.0, 99.5, 100.5, 0)
    update_chandelier_state(s, t, p, b, is_activation=True)
    assert s.chandelier_window_count == 1
    assert s.chandelier_rolling_high == 101.0
    assert s.chandelier_rolling_low is None  # long position only tracks high
    assert s.chandelier_frozen_params == {
        "lookback": 5, "atr_period": 3, "multiplier": 2.0, "ma_type": "RMA",
    }
    # No stop on the entry bar (don't fire on entry).
    assert s.chandelier_stop is None


def test_activation_short_seeds_low_not_high() -> None:
    s = TriggerState()
    t = _trig()
    p = _short()
    b = _bar(100.0, 101.0, 99.5, 100.5, 0)
    update_chandelier_state(s, t, p, b, is_activation=True)
    assert s.chandelier_rolling_low == 99.5
    assert s.chandelier_rolling_high is None


def test_non_chandelier_trigger_is_noop() -> None:
    s = TriggerState()
    t = ExitTrigger(kind=TriggerKind.MARKET)
    p = _long()
    b = _bar(100.0, 101.0, 99.0, 100.0, 0)
    update_chandelier_state(s, t, p, b, is_activation=True)
    # Nothing should have been seeded.
    assert s.chandelier_frozen_params is None
    assert s.chandelier_window_count == 0


def test_window_count_caps_at_lookback() -> None:
    s = TriggerState()
    t = _trig(chandelier_lookback=3)
    p = _long()
    update_chandelier_state(s, t, p, _bar(100, 100, 99, 99.5, 0), is_activation=True)
    for i in range(1, 10):
        update_chandelier_state(s, t, p, _bar(100, 100 + i, 99, 99.5, i), is_activation=False)
    assert s.chandelier_window_count == 3


def test_long_ratchet_does_not_descend_when_proposed_lower() -> None:
    s = TriggerState()
    t = _trig(chandelier_lookback=2, chandelier_atr_period=2, chandelier_multiplier=1.0)
    p = _long()
    # 4 bars: build a high then watch rolling-high stay anchored at it
    # via ratchet even after the window pages forward.
    update_chandelier_state(s, t, p, _bar(100, 100, 99, 99.5, 0), is_activation=True)
    update_chandelier_state(s, t, p, _bar(100, 110, 99, 100, 1), is_activation=False)
    update_chandelier_state(s, t, p, _bar(100, 110, 99, 100, 2), is_activation=False)
    stop_high = s.chandelier_stop
    assert stop_high is not None and stop_high > 0
    # Now a much-lower bar (high=101). Window slides forward; ratchet must hold.
    update_chandelier_state(s, t, p, _bar(100, 101, 99, 100, 3), is_activation=False)
    update_chandelier_state(s, t, p, _bar(100, 101, 99, 100, 4), is_activation=False)
    assert s.chandelier_stop >= stop_high


def test_short_ratchet_does_not_rise_when_proposed_higher() -> None:
    s = TriggerState()
    t = _trig(chandelier_lookback=2, chandelier_atr_period=2, chandelier_multiplier=1.0)
    p = _short()
    update_chandelier_state(s, t, p, _bar(100, 101, 100, 100.5, 0), is_activation=True)
    update_chandelier_state(s, t, p, _bar(100, 101, 90, 95, 1), is_activation=False)
    update_chandelier_state(s, t, p, _bar(100, 101, 90, 95, 2), is_activation=False)
    stop_low = s.chandelier_stop
    assert stop_low is not None
    # Lows climb back — proposed stop would rise, but ratchet pins it.
    update_chandelier_state(s, t, p, _bar(100, 101, 99, 100, 3), is_activation=False)
    update_chandelier_state(s, t, p, _bar(100, 101, 99, 100, 4), is_activation=False)
    assert s.chandelier_stop <= stop_low


def test_atr_warmup_keeps_stop_none() -> None:
    s = TriggerState()
    t = _trig(chandelier_atr_period=10)
    p = _long()
    update_chandelier_state(s, t, p, _bar(100, 100, 99, 99.5, 0), is_activation=True)
    # Not enough bars for atr_period=10 ATR to warm
    for i in range(1, 5):
        update_chandelier_state(s, t, p, _bar(100, 100, 99, 99.5, i), is_activation=False)
    assert s.chandelier_stop is None


def test_frozen_params_immutable_after_activation() -> None:
    s = TriggerState()
    t = _trig(chandelier_multiplier=2.0)
    p = _long()
    update_chandelier_state(s, t, p, _bar(100, 100, 99, 99.5, 0), is_activation=True)
    assert s.chandelier_frozen_params["multiplier"] == 2.0
    # Now mutate the trigger — frozen state must not change.
    t.chandelier_multiplier = 5.0
    update_chandelier_state(s, t, p, _bar(100, 101, 99, 100, 1), is_activation=False)
    assert s.chandelier_frozen_params["multiplier"] == 2.0


# ---------------------------------------------------------------------------
# evaluate_chandelier_stop
# ---------------------------------------------------------------------------


def test_evaluate_returns_no_fire_when_warming_up() -> None:
    s = TriggerState()
    t = _trig()
    p = _long()
    update_chandelier_state(s, t, p, _bar(100, 100, 99, 99.5, 0), is_activation=True)
    # Stop is None → no fire.
    d = evaluate_chandelier_stop(s, t, p, _bar(100, 100, 99, 99.5, 1))
    assert not d.fire


def test_evaluate_long_fires_on_touch() -> None:
    """Build a chandelier state with a known stop and verify long touch."""
    s = TriggerState()
    t = _trig(chandelier_lookback=2, chandelier_atr_period=2, chandelier_multiplier=1.0)
    p = _long()
    update_chandelier_state(s, t, p, _bar(100, 102, 99, 100, 0), is_activation=True)
    update_chandelier_state(s, t, p, _bar(100, 102, 99, 100, 1), is_activation=False)
    update_chandelier_state(s, t, p, _bar(100, 102, 99, 100, 2), is_activation=False)
    stop = s.chandelier_stop
    assert stop is not None
    # Bar that touches the stop (low <= stop, open above stop → no slippage)
    touch_bar = _bar(stop + 0.5, stop + 0.5, stop - 0.5, stop, 3)
    d = evaluate_chandelier_stop(s, t, p, touch_bar)
    assert d.fire
    assert d.fire_price == pytest.approx(stop)
    assert d.reason == "chandelier-long"
    # No realized slippage when open is above stop.
    assert s.chandelier_realized_slippage == pytest.approx(0.0)


def test_evaluate_long_gap_down_records_slippage() -> None:
    s = TriggerState()
    t = _trig(chandelier_lookback=2, chandelier_atr_period=2, chandelier_multiplier=1.0)
    p = _long()
    update_chandelier_state(s, t, p, _bar(100, 102, 99, 100, 0), is_activation=True)
    update_chandelier_state(s, t, p, _bar(100, 102, 99, 100, 1), is_activation=False)
    update_chandelier_state(s, t, p, _bar(100, 102, 99, 100, 2), is_activation=False)
    stop = s.chandelier_stop
    # Gap-down open below stop; fill at stop, slippage = stop - open.
    gap_open = stop - 1.0
    gap_bar = _bar(gap_open, gap_open + 0.2, gap_open - 0.5, gap_open, 3)
    d = evaluate_chandelier_stop(s, t, p, gap_bar)
    assert d.fire
    assert d.fire_price == pytest.approx(stop)
    assert s.chandelier_realized_slippage == pytest.approx(stop - gap_open)


def test_evaluate_short_fires_on_touch() -> None:
    s = TriggerState()
    t = _trig(chandelier_lookback=2, chandelier_atr_period=2, chandelier_multiplier=1.0)
    p = _short()
    update_chandelier_state(s, t, p, _bar(100, 101, 98, 99, 0), is_activation=True)
    update_chandelier_state(s, t, p, _bar(100, 101, 98, 99, 1), is_activation=False)
    update_chandelier_state(s, t, p, _bar(100, 101, 98, 99, 2), is_activation=False)
    stop = s.chandelier_stop
    assert stop is not None
    # Short fires when high >= stop.
    touch_bar = _bar(stop - 0.5, stop + 0.5, stop - 0.5, stop, 3)
    d = evaluate_chandelier_stop(s, t, p, touch_bar)
    assert d.fire
    assert d.reason == "chandelier-short"
    assert d.fire_price == pytest.approx(stop)
    assert s.chandelier_realized_slippage == pytest.approx(0.0)


def test_evaluate_short_gap_up_records_slippage() -> None:
    s = TriggerState()
    t = _trig(chandelier_lookback=2, chandelier_atr_period=2, chandelier_multiplier=1.0)
    p = _short()
    update_chandelier_state(s, t, p, _bar(100, 101, 98, 99, 0), is_activation=True)
    update_chandelier_state(s, t, p, _bar(100, 101, 98, 99, 1), is_activation=False)
    update_chandelier_state(s, t, p, _bar(100, 101, 98, 99, 2), is_activation=False)
    stop = s.chandelier_stop
    gap_open = stop + 1.0
    gap_bar = _bar(gap_open, gap_open + 0.5, gap_open - 0.2, gap_open, 3)
    d = evaluate_chandelier_stop(s, t, p, gap_bar)
    assert d.fire
    assert d.fire_price == pytest.approx(stop)
    assert s.chandelier_realized_slippage == pytest.approx(gap_open - stop)


def test_evaluate_no_fire_when_kind_mismatch() -> None:
    s = TriggerState()
    s.chandelier_stop = 95.0
    t = ExitTrigger(kind=TriggerKind.MARKET)  # not chandelier
    p = _long()
    d = evaluate_chandelier_stop(s, t, p, _bar(100, 100, 90, 95, 0))
    assert not d.fire


def test_evaluate_no_fire_when_position_flat() -> None:
    s = TriggerState()
    s.chandelier_stop = 95.0
    t = _trig()
    p = _long(qty=0.0)  # flat
    p.qty_open = 0.0
    d = evaluate_chandelier_stop(s, t, p, _bar(100, 100, 90, 95, 0))
    assert not d.fire
