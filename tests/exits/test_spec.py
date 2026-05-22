"""Tests for ``exits.spec`` — pure native-trigger evaluators."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from tradinglab.exits.model import (
    ActivationUnit,
    ExitTrigger,
    TrailBasis,
    TrailUnit,
    TriggerKind,
)
from tradinglab.exits.spec import (
    Bar,
    Decision,
    TriggerState,
    compute_initial_risk_per_share,
    compute_qty_at_fire,
    evaluate_limit,
    evaluate_market,
    evaluate_stop,
    evaluate_stop_limit,
    evaluate_time_of_day,
    evaluate_trailing_stop,
    recompute_hwm_from_history,
    resolve_price,
    update_trail_state,
)
from tradinglab.positions.model import Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _long(entry: float = 100.0, qty: float = 100.0) -> Position:
    return Position(
        id="P1",
        symbol="AAPL",
        side="long",
        qty_initial=qty,
        qty_open=qty,
        avg_entry_price=entry,
        entry_time=datetime(2024, 1, 2, 9, 30),
        source="manual",
    )


def _short(entry: float = 100.0, qty: float = 100.0) -> Position:
    return Position(
        id="P2",
        symbol="AAPL",
        side="short",
        qty_initial=qty,
        qty_open=qty,
        avg_entry_price=entry,
        entry_time=datetime(2024, 1, 2, 9, 30),
        source="manual",
    )


def _bar(o=100.0, h=101.0, l=99.0, c=100.5) -> Bar:
    return Bar(open=o, high=h, low=l, close=c)


# ---------------------------------------------------------------------------
# resolve_price
# ---------------------------------------------------------------------------


def test_resolve_price_explicit():
    t = ExitTrigger(kind=TriggerKind.LIMIT, price=150.0)
    assert resolve_price(t, _long(100.0)) == 150.0


def test_resolve_price_offset_pct():
    t = ExitTrigger(kind=TriggerKind.LIMIT, offset_pct=2.0)
    assert resolve_price(t, _long(100.0)) == pytest.approx(102.0)


def test_resolve_price_offset_dollar():
    t = ExitTrigger(kind=TriggerKind.LIMIT, offset_dollar=-1.5)
    assert resolve_price(t, _long(100.0)) == pytest.approx(98.5)


def test_resolve_price_unset_returns_none():
    t = ExitTrigger(kind=TriggerKind.LIMIT)
    assert resolve_price(t, _long(100.0)) is None


def test_resolve_price_stop_limit_uses_stop_limit_price():
    t = ExitTrigger(
        kind=TriggerKind.STOP_LIMIT, price=180.0, stop_limit_price=179.5
    )
    assert resolve_price(t, _long(200.0), use_stop_limit=False) == 180.0
    assert resolve_price(t, _long(200.0), use_stop_limit=True) == 179.5


def test_resolve_price_stop_limit_offset_relative_to_stop():
    t = ExitTrigger(kind=TriggerKind.STOP_LIMIT, price=180.0, stop_limit_offset=-0.5)
    assert resolve_price(t, _long(200.0), use_stop_limit=True) == 179.5


# ---------------------------------------------------------------------------
# qty_pct fire-time resolution (B6)
# ---------------------------------------------------------------------------


def test_compute_qty_at_fire_default_100pct():
    pos = _long(100.0, qty=200.0)
    t = ExitTrigger()
    assert compute_qty_at_fire(t, pos) == 200.0


def test_compute_qty_at_fire_50pct_resolves_against_qty_open():
    pos = _long(100.0, qty=200.0)
    pos.qty_open = 150.0  # partial close already happened
    t = ExitTrigger(qty_pct=50.0)
    # 50% of CURRENT qty_open, not initial
    assert compute_qty_at_fire(t, pos) == 75.0


def test_compute_qty_at_fire_flat_returns_zero():
    pos = _long(100.0, qty=200.0)
    pos.qty_open = 0.0
    assert compute_qty_at_fire(ExitTrigger(), pos) == 0.0


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------


def test_market_fires_immediately_long():
    d = evaluate_market(
        ExitTrigger(kind=TriggerKind.MARKET), _long(100.0, qty=100.0), _bar(c=99.5)
    )
    assert d.fire is True
    assert d.fire_price == 99.5
    assert d.qty == 100.0


def test_market_no_fire_when_position_flat():
    pos = _long(100.0, qty=100.0)
    pos.qty_open = 0.0
    d = evaluate_market(ExitTrigger(kind=TriggerKind.MARKET), pos, _bar())
    assert d.fire is False


def test_market_kind_mismatch_no_fire():
    d = evaluate_market(ExitTrigger(kind=TriggerKind.LIMIT, price=100), _long(), _bar())
    assert d.fire is False


# ---------------------------------------------------------------------------
# Limit (touched-through)
# ---------------------------------------------------------------------------


def test_limit_long_fires_on_touched_high():
    t = ExitTrigger(kind=TriggerKind.LIMIT, price=105.0)
    d = evaluate_limit(t, _long(100.0), _bar(o=100, h=106, l=99, c=104))
    assert d.fire is True
    assert d.fire_price == 105.0


def test_limit_long_no_fire_below_target():
    t = ExitTrigger(kind=TriggerKind.LIMIT, price=105.0)
    d = evaluate_limit(t, _long(100.0), _bar(o=100, h=104, l=99, c=103))
    assert d.fire is False


def test_limit_short_fires_on_touched_low():
    t = ExitTrigger(kind=TriggerKind.LIMIT, price=95.0)
    d = evaluate_limit(t, _short(100.0), _bar(o=100, h=101, l=94, c=96))
    assert d.fire is True
    assert d.fire_price == 95.0


def test_limit_short_no_fire_above_target():
    t = ExitTrigger(kind=TriggerKind.LIMIT, price=95.0)
    d = evaluate_limit(t, _short(100.0), _bar(o=100, h=101, l=96, c=97))
    assert d.fire is False


# ---------------------------------------------------------------------------
# Stop (touched-through, gap-through)
# ---------------------------------------------------------------------------


def test_stop_long_fires_on_touched_low():
    t = ExitTrigger(kind=TriggerKind.STOP, price=95.0)
    d = evaluate_stop(t, _long(100.0), _bar(o=99, h=100, l=94, c=96))
    assert d.fire is True
    assert d.fire_price == 95.0


def test_stop_long_gap_through_fills_at_open():
    t = ExitTrigger(kind=TriggerKind.STOP, price=95.0)
    # Gap-down open — bar.open=90, low=89 (way below stop)
    d = evaluate_stop(t, _long(100.0), _bar(o=90, h=91, l=89, c=89.5))
    assert d.fire is True
    assert d.fire_price == 90.0  # gap fill at open


def test_stop_short_fires_on_touched_high():
    t = ExitTrigger(kind=TriggerKind.STOP, price=105.0)
    d = evaluate_stop(t, _short(100.0), _bar(o=101, h=106, l=100, c=104))
    assert d.fire is True
    assert d.fire_price == 105.0


def test_stop_short_gap_through_fills_at_open():
    t = ExitTrigger(kind=TriggerKind.STOP, price=105.0)
    # Gap-up open — bar.open=110, high=111
    d = evaluate_stop(t, _short(100.0), _bar(o=110, h=111, l=109, c=110.5))
    assert d.fire is True
    assert d.fire_price == 110.0


# ---------------------------------------------------------------------------
# Stop-limit
# ---------------------------------------------------------------------------


def test_stop_limit_long_normal_fill():
    t = ExitTrigger(
        kind=TriggerKind.STOP_LIMIT, price=95.0, stop_limit_price=94.5
    )
    # Stop touched (low=94), limit fillable (high=95 covers 94.5)
    d = evaluate_stop_limit(t, _long(100.0), _bar(o=95, h=95.2, l=94, c=94.8))
    assert d.fire is True
    assert d.fire_price == 94.5
    assert d.limit_price == 94.5


def test_stop_limit_long_gap_through_no_fill():
    t = ExitTrigger(
        kind=TriggerKind.STOP_LIMIT, price=95.0, stop_limit_price=94.5
    )
    # Gap-down: opens at 92 (way below limit) — limit order would sit unfilled
    d = evaluate_stop_limit(t, _long(100.0), _bar(o=92, h=92.5, l=90, c=91))
    assert d.fire is False


def test_stop_limit_short_normal_fill():
    t = ExitTrigger(
        kind=TriggerKind.STOP_LIMIT, price=105.0, stop_limit_price=105.5
    )
    d = evaluate_stop_limit(t, _short(100.0), _bar(o=105, h=106, l=104.8, c=105.3))
    assert d.fire is True
    assert d.fire_price == 105.5


def test_stop_limit_short_gap_through_no_fill():
    t = ExitTrigger(
        kind=TriggerKind.STOP_LIMIT, price=105.0, stop_limit_price=105.5
    )
    # Gap-up: opens at 108 (above limit)
    d = evaluate_stop_limit(t, _short(100.0), _bar(o=108, h=110, l=107, c=109))
    assert d.fire is False


# ---------------------------------------------------------------------------
# Trailing stop — HWM updates per trail_basis
# ---------------------------------------------------------------------------


def test_trail_basis_close_skips_forming_hwm_update():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.PERCENT,
        trail_value=2.0,
        trail_basis=TrailBasis.CLOSE,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=110, l=99, c=105), is_close=False)
    assert state.hwm is None  # forming bar didn't move HWM


def test_trail_basis_close_updates_hwm_on_close():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.PERCENT,
        trail_value=2.0,
        trail_basis=TrailBasis.CLOSE,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=110, l=99, c=105), is_close=True)
    assert state.hwm == 105.0  # close-only basis uses bar.close


def test_trail_basis_intrabar_updates_hwm_to_high():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.PERCENT,
        trail_value=2.0,
        trail_basis=TrailBasis.INTRABAR,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=110, l=99, c=105), is_close=False)
    assert state.hwm == 110.0  # uses bar.high on intrabar


def test_trail_basis_intrabar_short_uses_low():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.PERCENT,
        trail_value=2.0,
        trail_basis=TrailBasis.INTRABAR,
    )
    state = TriggerState()
    pos = _short(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=101, l=92, c=95), is_close=False)
    assert state.lwm == 92.0


# ---------------------------------------------------------------------------
# Trailing stop — activation gate
# ---------------------------------------------------------------------------


def test_trail_no_activation_gate_armed_immediately():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.PERCENT,
        trail_value=2.0,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=101, l=99, c=100.5), is_close=True)
    assert state.activated is True
    assert state.trail_price is not None


def test_trail_activation_pct_not_yet_satisfied():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.PERCENT,
        trail_value=2.0,
        activation_unit=ActivationUnit.PERCENT,
        activation_value=5.0,  # need +5% before arming
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=103, l=99, c=102), is_close=True)
    assert state.activated is False
    assert state.trail_price is None


def test_trail_activation_pct_satisfied_arms_trail():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.PERCENT,
        trail_value=2.0,
        trail_basis=TrailBasis.CLOSE,
        activation_unit=ActivationUnit.PERCENT,
        activation_value=5.0,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=106, l=99, c=105), is_close=True)
    assert state.activated is True
    # HWM=105 (close basis), trail offset = 105 * 2% = 2.1, trail_price=102.9
    assert state.trail_price == pytest.approx(102.9)


def test_trail_activation_r_multiple_uses_paired_stop():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.DOLLAR,
        trail_value=1.0,
        activation_unit=ActivationUnit.R_MULTIPLE,
        activation_value=1.0,  # need 1R favorable
    )
    state = TriggerState()
    pos = _long(100.0)
    paired_stop = 95.0  # 1R = $5
    # Bar with high=104 (less than 1R = need >=105) shouldn't activate
    update_trail_state(
        state, t, pos, _bar(o=100, h=104, l=99, c=103), is_close=True, paired_stop_price=paired_stop
    )
    assert state.activated is False
    # Bar with close=106 (close basis: peak=106 >= entry+1R=105)
    update_trail_state(
        state, t, pos, _bar(o=104, h=107, l=103, c=106), is_close=True, paired_stop_price=paired_stop
    )
    assert state.activated is True


# ---------------------------------------------------------------------------
# Trailing stop — ratchet (trail never loosens)
# ---------------------------------------------------------------------------


def test_trail_ratchet_long():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.DOLLAR,
        trail_value=1.0,
        trail_basis=TrailBasis.CLOSE,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=110, l=99, c=109), is_close=True)
    assert state.trail_price == pytest.approx(108.0)  # close-basis: hwm=109, -1
    # New bar: hwm doesn't increase (close=107). Trail must NOT loosen.
    update_trail_state(state, t, pos, _bar(o=109, h=109.5, l=106, c=107), is_close=True)
    assert state.hwm == 109.0  # ratcheted
    assert state.trail_price == pytest.approx(108.0)  # ratcheted


def test_trail_ratchet_short():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.DOLLAR,
        trail_value=1.0,
        trail_basis=TrailBasis.CLOSE,
    )
    state = TriggerState()
    pos = _short(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=101, l=90, c=91), is_close=True)
    assert state.lwm == 91.0  # close-basis
    assert state.trail_price == pytest.approx(92.0)  # lwm + $1
    update_trail_state(state, t, pos, _bar(o=91, h=94, l=90.5, c=93), is_close=True)
    assert state.lwm == 91.0  # ratcheted
    assert state.trail_price == pytest.approx(92.0)


# ---------------------------------------------------------------------------
# Trailing stop — fires on touched-through
# ---------------------------------------------------------------------------


def test_trail_fires_when_touched_long():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.DOLLAR,
        trail_value=2.0,
        trail_basis=TrailBasis.CLOSE,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=105, l=99, c=104), is_close=True)
    assert state.trail_price == pytest.approx(102.0)
    # Next bar: low=101, breaches trail at 102 → fires at 102 (no gap)
    bar = _bar(o=104, h=104.5, l=101, c=101.5)
    d = evaluate_trailing_stop(state, t, pos, bar)
    assert d.fire is True
    assert d.fire_price == pytest.approx(102.0)


def test_trail_no_fire_when_not_yet_activated():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.DOLLAR,
        trail_value=1.0,
        activation_unit=ActivationUnit.PERCENT,
        activation_value=10.0,  # need +10%
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=105, l=99, c=104), is_close=True)
    d = evaluate_trailing_stop(state, t, pos, _bar(o=104, h=105, l=98, c=99))
    assert d.fire is False


def test_trail_atr_unit_uses_atr_value():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.ATR,
        trail_value=2.0,  # 2x ATR
        trail_basis=TrailBasis.CLOSE,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(
        state, t, pos, _bar(o=100, h=110, l=99, c=109), is_close=True, atr_value=1.5
    )
    # offset = 2 * 1.5 = 3.0; hwm=109 → trail=106
    assert state.trail_price == pytest.approx(106.0)


def test_trail_atr_without_atr_value_skips_trail_set():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.ATR,
        trail_value=2.0,
        trail_basis=TrailBasis.CLOSE,
    )
    state = TriggerState()
    pos = _long(100.0)
    update_trail_state(state, t, pos, _bar(o=100, h=110, l=99, c=109), is_close=True)
    # HWM still set, but trail_price not because ATR was None
    assert state.hwm == 109.0
    assert state.trail_price is None


# ---------------------------------------------------------------------------
# Recompute HWM on bar correction
# ---------------------------------------------------------------------------


def test_recompute_hwm_resets_state_then_replays():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.DOLLAR,
        trail_value=1.0,
        trail_basis=TrailBasis.CLOSE,
    )
    state = TriggerState()
    pos = _long(100.0)
    # Pre-correction: a forming-bar tick set HWM to 110 erroneously
    state.hwm = 110.0
    state.trail_price = 109.0
    state.activated = True
    bars_known_good = [
        _bar(o=100, h=102, l=99, c=101),
        _bar(o=101, h=103, l=100.5, c=102.5),
    ]
    recompute_hwm_from_history(state, t, pos, bars_known_good)
    # HWM now reflects only known-good bars: max close = 102.5
    assert state.hwm == 102.5
    assert state.trail_price == pytest.approx(101.5)


# ---------------------------------------------------------------------------
# Time-of-day
# ---------------------------------------------------------------------------


def test_time_of_day_fires_after_cutoff():
    t = ExitTrigger(kind=TriggerKind.TIME_OF_DAY, time_of_day="15:55")
    pos = _long(100.0)
    bar = _bar()
    now = datetime(2024, 1, 2, 15, 55, 0)
    d = evaluate_time_of_day(t, pos, bar, now=now)
    assert d.fire is True


def test_time_of_day_no_fire_before_cutoff():
    t = ExitTrigger(kind=TriggerKind.TIME_OF_DAY, time_of_day="15:55")
    pos = _long(100.0)
    bar = _bar()
    now = datetime(2024, 1, 2, 15, 30, 0)
    d = evaluate_time_of_day(t, pos, bar, now=now)
    assert d.fire is False


def test_time_of_day_malformed_no_fire():
    t = ExitTrigger(kind=TriggerKind.TIME_OF_DAY, time_of_day="not-a-time")
    pos = _long(100.0)
    d = evaluate_time_of_day(t, pos, _bar(), now=datetime(2024, 1, 2, 15, 55))
    assert d.fire is False


# ---------------------------------------------------------------------------
# Risk helper
# ---------------------------------------------------------------------------


def test_compute_initial_risk_per_share():
    pos = _long(100.0)
    assert compute_initial_risk_per_share(pos, 95.0) == 5.0


def test_compute_initial_risk_per_share_no_stop_returns_none():
    pos = _long(100.0)
    assert compute_initial_risk_per_share(pos, None) is None


def test_compute_initial_risk_per_share_zero_risk_returns_none():
    pos = _long(100.0)
    assert compute_initial_risk_per_share(pos, 100.0) is None
