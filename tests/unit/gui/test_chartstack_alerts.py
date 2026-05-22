"""Unit tests for M6 ChartStack four-tier alert engine.

These tests are headless — they construct :class:`AlertEngine`
directly with deterministic clock + chime injection. The few
panel-integration tests use the ``root`` fixture from the
top-level conftest to avoid the per-process ``tk.Tk()`` init
flake on Windows ARM64.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from typing import Any

import pytest

from tradinglab.gui.chartstack import alerts as A
from tradinglab.gui.chartstack import owner_state, settings_adapter
from tradinglab.gui.chartstack.alerts import (
    AlertEngine,
    AlertResult,
    AlertTier,
    evaluate_tier1_atr_expansion,
    evaluate_tier1_rvol_spike,
    evaluate_tier2_new_scanner_edge,
    evaluate_tier2_pmh_pml_break,
    evaluate_tier3_mae_one_r,
    evaluate_tier3_pnl_zero_cross,
    evaluate_tier3_stop_proximity,
    evaluate_tier4_earnings_t1,
    evaluate_tier4_exdiv_today,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(o, h, lo, c, v, *, session="regular"):
    return SimpleNamespace(open=o, high=h, low=lo, close=c, volume=v,
                           session=session)


def _flat_bars(n: int, *, vol: float = 100.0, session: str = "regular"):
    """Return ``n`` identical flat bars (no TR, no RVOL spike)."""
    return [_bar(100.0, 100.0, 100.0, 100.0, vol, session=session)
            for _ in range(n)]


# ---------------------------------------------------------------------------
# Tier-1 RVOL spike
# ---------------------------------------------------------------------------


def test_tier1_rvol_returns_none_with_too_few_bars():
    bars = _flat_bars(10, vol=100.0)
    assert evaluate_tier1_rvol_spike(
        bars, interval_minutes=5,
        rvol_1m_threshold=2.5, rvol_5m_threshold=1.8) is None


def test_tier1_rvol_fires_on_5m_at_1p8x():
    bars = _flat_bars(20, vol=100.0) + [_bar(100, 100, 100, 100, 200.0)]
    rid = evaluate_tier1_rvol_spike(
        bars, interval_minutes=5,
        rvol_1m_threshold=2.5, rvol_5m_threshold=1.8)
    assert rid == "tier1_rvol_spike"


def test_tier1_rvol_quiet_below_threshold():
    bars = _flat_bars(20, vol=100.0) + [_bar(100, 100, 100, 100, 150.0)]
    rid = evaluate_tier1_rvol_spike(
        bars, interval_minutes=5,
        rvol_1m_threshold=2.5, rvol_5m_threshold=1.8)
    assert rid is None


def test_tier1_rvol_uses_1m_threshold_when_interval_is_1():
    # 2.0x — fires on 5m (1.8) but not 1m (2.5).
    bars = _flat_bars(20, vol=100.0) + [_bar(100, 100, 100, 100, 200.0)]
    assert evaluate_tier1_rvol_spike(
        bars, interval_minutes=1,
        rvol_1m_threshold=2.5, rvol_5m_threshold=1.8) is None
    assert evaluate_tier1_rvol_spike(
        bars, interval_minutes=1,
        rvol_1m_threshold=1.5, rvol_5m_threshold=1.8) == "tier1_rvol_spike"


def test_tier1_rvol_ignores_pre_market_bars():
    pre = [_bar(100, 100, 100, 100, 1000.0, session="pre")
           for _ in range(20)]
    reg = _flat_bars(20, vol=100.0) + [_bar(100, 100, 100, 100, 200.0)]
    bars = pre + reg
    assert evaluate_tier1_rvol_spike(
        bars, interval_minutes=5,
        rvol_1m_threshold=2.5, rvol_5m_threshold=1.8) == "tier1_rvol_spike"


# ---------------------------------------------------------------------------
# Tier-1 ATR expansion
# ---------------------------------------------------------------------------


def test_tier1_atr_returns_none_with_too_few_bars():
    bars = _flat_bars(10)
    assert evaluate_tier1_atr_expansion(bars, atr_threshold=1.8) is None


def test_tier1_atr_quiet_on_flat_bars():
    bars = _flat_bars(20)
    assert evaluate_tier1_atr_expansion(bars, atr_threshold=1.8) is None


def test_tier1_atr_fires_on_large_range_bar():
    # 14 bars of TR=1 + one TR=3 → 3/1 = 3.0 > 1.8.
    base = [_bar(100, 101, 100, 100.5, 100) for _ in range(14)]
    spike = _bar(100, 103, 100, 102, 100)
    bars = base + [spike]
    rid = evaluate_tier1_atr_expansion(bars, atr_threshold=1.8)
    assert rid == "tier1_atr_expansion"


# ---------------------------------------------------------------------------
# Tier-2 PMH/PML break
# ---------------------------------------------------------------------------


def test_tier2_pmh_break_when_close_above_premarket_high():
    pre = [_bar(100, 101, 99, 100, 100, session="pre")]
    reg = [_bar(100.5, 102, 100, 101.5, 100)]  # 101.5 > 101
    assert evaluate_tier2_pmh_pml_break(pre + reg) == "tier2_pmh_break"


def test_tier2_pml_break_when_close_below_premarket_low():
    pre = [_bar(100, 101, 99, 100, 100, session="pre")]
    reg = [_bar(100, 100, 97, 98, 100)]  # 98 < 99
    assert evaluate_tier2_pmh_pml_break(pre + reg) == "tier2_pml_break"


def test_tier2_pmh_pml_quiet_when_inside_range():
    pre = [_bar(100, 102, 98, 100, 100, session="pre")]
    reg = [_bar(100, 101, 99, 100, 100)]
    assert evaluate_tier2_pmh_pml_break(pre + reg) is None


def test_tier2_pmh_pml_returns_none_without_premarket():
    reg = [_bar(100, 101, 99, 100.5, 100)]
    assert evaluate_tier2_pmh_pml_break(reg) is None


# ---------------------------------------------------------------------------
# Tier-2 new scanner edge
# ---------------------------------------------------------------------------


def test_tier2_new_scanner_edge_when_is_new():
    row = SimpleNamespace(symbol="AAPL", is_new=True)
    assert evaluate_tier2_new_scanner_edge(row) == "tier2_new_scanner_edge"


def test_tier2_new_scanner_edge_quiet_when_not_new():
    row = SimpleNamespace(symbol="AAPL", is_new=False)
    assert evaluate_tier2_new_scanner_edge(row) is None


def test_tier2_new_scanner_edge_handles_none():
    assert evaluate_tier2_new_scanner_edge(None) is None


# ---------------------------------------------------------------------------
# Tier-3 stop proximity
# ---------------------------------------------------------------------------


def test_tier3_stop_proximity_quiet_without_position():
    assert evaluate_tier3_stop_proximity(
        _flat_bars(20), position=None, atr_window=0.3) is None


def test_tier3_stop_proximity_quiet_without_stop_price():
    pos = SimpleNamespace(stop_price=None, symbol="AAPL")
    assert evaluate_tier3_stop_proximity(
        _flat_bars(20), position=pos, atr_window=0.3) is None


def test_tier3_stop_proximity_fires_when_close_within_window():
    # ATR(14) on 14 base bars + 1 quiet last bar settles ~0.98;
    # close=100, stop=99.85 → diff 0.15 < 0.3 × 0.98 ≈ 0.29.
    base = [_bar(100, 101, 100, 100, 100) for _ in range(14)]
    last = _bar(100, 100.5, 99.8, 100.0, 100)
    bars = base + [last]
    pos = SimpleNamespace(stop_price=99.85, symbol="AAPL")
    rid = evaluate_tier3_stop_proximity(bars, position=pos, atr_window=0.3)
    assert rid == "tier3_stop_proximity"


def test_tier3_stop_proximity_quiet_when_far_from_stop():
    bars = [_bar(100, 101, 100, 100, 100) for _ in range(15)]
    pos = SimpleNamespace(stop_price=50.0, symbol="AAPL")
    assert evaluate_tier3_stop_proximity(
        bars, position=pos, atr_window=0.3) is None


# ---------------------------------------------------------------------------
# Tier-3 P&L zero cross
# ---------------------------------------------------------------------------


def test_tier3_pnl_zero_cross_fires_on_sign_change():
    pos = SimpleNamespace(unrealized_pnl=5.0)
    rid = evaluate_tier3_pnl_zero_cross(position=pos, prev_unrealized=-3.0)
    assert rid == "tier3_pnl_zero_cross"


def test_tier3_pnl_zero_cross_quiet_on_same_sign():
    pos = SimpleNamespace(unrealized_pnl=5.0)
    assert evaluate_tier3_pnl_zero_cross(
        position=pos, prev_unrealized=2.0) is None


def test_tier3_pnl_zero_cross_quiet_without_prev():
    pos = SimpleNamespace(unrealized_pnl=5.0)
    assert evaluate_tier3_pnl_zero_cross(
        position=pos, prev_unrealized=None) is None


# ---------------------------------------------------------------------------
# Tier-3 MAE ≥ 1R
# ---------------------------------------------------------------------------


def test_tier3_mae_fires_at_one_r():
    pos = SimpleNamespace(mae_r=1.0)
    assert evaluate_tier3_mae_one_r(position=pos) == "tier3_mae_one_r"


def test_tier3_mae_quiet_below_one_r():
    pos = SimpleNamespace(mae_r=0.5)
    assert evaluate_tier3_mae_one_r(position=pos) is None


def test_tier3_mae_derives_from_abs_and_risk():
    pos = SimpleNamespace(mae_r=None, mae_abs=2.0, risk_abs=2.0)
    assert evaluate_tier3_mae_one_r(position=pos) == "tier3_mae_one_r"


# ---------------------------------------------------------------------------
# Tier-4 earnings / ex-div
# ---------------------------------------------------------------------------


def test_tier4_earnings_t1_fires_when_one_day():
    assert evaluate_tier4_earnings_t1(days_to_earnings=1) == "tier4_earnings_t1"


def test_tier4_earnings_t1_quiet_when_two_days():
    assert evaluate_tier4_earnings_t1(days_to_earnings=2) is None


def test_tier4_earnings_t1_quiet_when_none():
    assert evaluate_tier4_earnings_t1(days_to_earnings=None) is None


def test_tier4_exdiv_today_fires_when_true():
    assert evaluate_tier4_exdiv_today(is_exdiv_today=True) == "tier4_exdiv_today"


def test_tier4_exdiv_today_quiet_when_false():
    assert evaluate_tier4_exdiv_today(is_exdiv_today=False) is None


# ---------------------------------------------------------------------------
# Time-of-day gate
# ---------------------------------------------------------------------------


def _utc_at(et_hour: int, et_minute: int) -> _dt.datetime:
    """Return a tz-aware UTC datetime that maps to the given ET time today.

    Reads ET via the same ``_ET`` resolver the engine uses so DST and
    timezone-database changes follow the engine.
    """
    today = _dt.date.today()
    et_naive = _dt.datetime(today.year, today.month, today.day,
                            et_hour, et_minute, 0)
    et_aware = et_naive.replace(tzinfo=A._ET)
    return et_aware.astimezone(_dt.timezone.utc)


def test_tod_gate_off_during_first_five_minutes():
    """09:30–09:35 ET → Tier-1 evaluators should not fire."""
    factor = A._time_of_day_factor(_utc_at(9, 32))
    assert factor is None


def test_tod_gate_tightened_until_ten_oclock():
    factor = A._time_of_day_factor(_utc_at(9, 45))
    assert factor == 2.0


def test_tod_gate_defaults_after_ten():
    factor = A._time_of_day_factor(_utc_at(10, 30))
    assert factor == 1.0


# ---------------------------------------------------------------------------
# AlertEngine integration
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_clock():
    """A mutable [now_utc] holder + a clock callable."""
    state = {"now": _utc_at(11, 0)}  # outside the ToD gate

    def _clock():
        return state["now"]
    return state, _clock


@pytest.fixture
def chime_calls():
    calls = []
    return calls, lambda: calls.append(1)


def test_engine_no_alerts_returns_none(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    res = eng.evaluate(0)
    assert res.tier is AlertTier.NONE
    assert not res.is_active
    assert calls == []


def test_engine_tier1_rvol_fires_amber(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    bars = _flat_bars(20, vol=100.0) + [_bar(100, 100, 100, 100, 500.0)]
    res = eng.evaluate(0, bars=bars, interval_minutes=5)
    assert res.tier is AlertTier.TIER_1_AMBER
    assert "tier1_rvol_spike" in res.rule_ids
    # Tier-1 is visual-only — no chime.
    assert calls == []


def test_engine_tier2_pmh_break_plays_one_chime(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    pre = [_bar(100, 101, 99, 100, 100, session="pre")]
    reg = [_bar(100, 102, 100, 101.5, 100)]
    res = eng.evaluate(0, bars=pre + reg)
    assert res.tier is AlertTier.TIER_2_BLUE
    assert "tier2_pmh_break" in res.rule_ids
    assert calls == [1]


def test_engine_tier2_pmh_break_is_edge_triggered(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    pre = [_bar(100, 101, 99, 100, 100, session="pre")]
    reg = [_bar(100, 102, 100, 101.5, 100)]
    eng.evaluate(0, bars=pre + reg)
    eng.evaluate(0, bars=pre + reg)  # second tick, same state
    # Only the first transition fires the chime.
    assert calls == [1]


def test_engine_audio_mute_silences_chimes(engine_clock, chime_calls, monkeypatch):
    state, clk = engine_clock
    calls, play = chime_calls
    monkeypatch.setitem(settings_adapter.DEFAULTS,
                        "chartstack.alerts.audio_muted", True)
    eng = AlertEngine(clock=clk, play_chime=play)
    pre = [_bar(100, 101, 99, 100, 100, session="pre")]
    reg = [_bar(100, 102, 100, 101.5, 100)]
    res = eng.evaluate(0, bars=pre + reg)
    assert res.tier is AlertTier.TIER_2_BLUE
    assert calls == []


def test_engine_rate_limit_caps_at_two_per_window(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    # Fire three independent Tier-2 chimes via the scanner edge,
    # all within one 10-second window.
    row = SimpleNamespace(symbol="AAPL", is_new=True)
    eng.evaluate(0, scanner_row=row)
    state["now"] = state["now"] + _dt.timedelta(seconds=1)
    eng.evaluate(1, scanner_row=row)
    state["now"] = state["now"] + _dt.timedelta(seconds=1)
    eng.evaluate(2, scanner_row=row)
    assert len(calls) == 2


def test_engine_rate_limit_recovers_after_window(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    row = SimpleNamespace(symbol="AAPL", is_new=True)
    eng.evaluate(0, scanner_row=row)
    eng.evaluate(1, scanner_row=row)
    # Two chimes burned. Wait past the window.
    state["now"] = state["now"] + _dt.timedelta(seconds=11)
    eng.evaluate(2, scanner_row=row)
    assert len(calls) == 3


def test_engine_tier3_bypasses_rate_limit(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    # Burn the rate limit with Tier-2.
    row = SimpleNamespace(symbol="AAPL", is_new=True)
    eng.evaluate(0, scanner_row=row)
    eng.evaluate(1, scanner_row=row)
    # Cap reached — a non-Tier-3 alert should be silent.
    state["now"] = state["now"] + _dt.timedelta(seconds=1)
    eng.evaluate(2, scanner_row=row)
    cap_calls = len(calls)
    assert cap_calls == 2
    # Tier-3 in the next tick: bypasses the cap (2 chimes).
    state["now"] = state["now"] + _dt.timedelta(seconds=1)
    pos = SimpleNamespace(stop_price=None, mae_r=1.5, unrealized_pnl=-5.0)
    eng.evaluate(3, position=pos)
    assert len(calls) == cap_calls + 2


def test_engine_tier3_paces_per_slot_at_five_seconds(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    pos = SimpleNamespace(stop_price=None, mae_r=1.5, unrealized_pnl=-5.0)
    eng.evaluate(0, position=pos)
    burst1 = len(calls)
    # Tick again within 5 s — no new ping.
    state["now"] = state["now"] + _dt.timedelta(seconds=2)
    eng.evaluate(0, position=pos)
    assert len(calls) == burst1
    # Now wait past the 5 s pacing window — new ping.
    state["now"] = state["now"] + _dt.timedelta(seconds=4)
    eng.evaluate(0, position=pos)
    assert len(calls) == burst1 + 2


def test_engine_returns_highest_tier(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    # Tier-1 + Tier-3 coexist → result must be Tier-3.
    bars = _flat_bars(20, vol=100.0) + [_bar(100, 100, 100, 100, 500.0)]
    pos = SimpleNamespace(stop_price=None, mae_r=1.5, unrealized_pnl=-5.0)
    res = eng.evaluate(0, bars=bars, position=pos)
    assert res.tier is AlertTier.TIER_3_RED


def test_engine_during_first_five_minutes_silences_tier1(
        engine_clock, chime_calls):
    state, clk = engine_clock
    # Move clock into the 09:32 ET window.
    state["now"] = _utc_at(9, 32)
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    bars = _flat_bars(20, vol=100.0) + [_bar(100, 100, 100, 100, 500.0)]
    res = eng.evaluate(0, bars=bars)
    assert res.tier is AlertTier.NONE


def test_engine_tightens_tier1_thresholds_between_935_and_10(
        engine_clock, chime_calls, monkeypatch):
    state, clk = engine_clock
    state["now"] = _utc_at(9, 50)
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    # 3x RVOL — passes the regular 1.8 threshold but not the 2x-tightened
    # 3.6 threshold.
    bars = _flat_bars(20, vol=100.0) + [_bar(100, 100, 100, 100, 300.0)]
    res = eng.evaluate(0, bars=bars, interval_minutes=5)
    assert res.tier is AlertTier.NONE


def test_engine_reset_clears_per_slot_state(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    pre = [_bar(100, 101, 99, 100, 100, session="pre")]
    reg = [_bar(100, 102, 100, 101.5, 100)]
    eng.evaluate(0, bars=pre + reg)
    eng.evaluate(0, bars=pre + reg)
    # Second eval is quiet — edge already consumed.
    assert calls == [1]
    eng.reset(slot_index=0)
    # After reset, the next eval re-fires.
    eng.evaluate(0, bars=pre + reg)
    assert calls == [1, 1]


def test_engine_reset_all_clears_global_window(engine_clock, chime_calls):
    state, clk = engine_clock
    calls, play = chime_calls
    eng = AlertEngine(clock=clk, play_chime=play)
    row = SimpleNamespace(symbol="AAPL", is_new=True)
    eng.evaluate(0, scanner_row=row)
    eng.evaluate(1, scanner_row=row)
    assert len(calls) == 2
    # Cap is hit; reset clears it.
    eng.reset()
    eng.evaluate(2, scanner_row=row)
    assert len(calls) == 3


def test_alert_result_color_maps_to_tier_tokens():
    assert AlertResult(tier=AlertTier.TIER_1_AMBER).color == "#a36b00"
    assert AlertResult(tier=AlertTier.TIER_2_BLUE).color == "#1f6feb"
    assert AlertResult(tier=AlertTier.TIER_3_RED).color == "#a33333"
    assert AlertResult(tier=AlertTier.TIER_4_YELLOW).color == "#d4a017"
    assert AlertResult(tier=AlertTier.NONE).color is None


# ---------------------------------------------------------------------------
# owner_state helpers
# ---------------------------------------------------------------------------


class _StubMatchRow:
    def __init__(self, symbol, rank_value=None, is_new=False):
        self.symbol = symbol
        self.rank_value = rank_value
        self.is_new = is_new


class _StubScanResult:
    def __init__(self, rows):
        self._rows = rows

    def matched_rows(self):
        return list(self._rows)


class _StubOwner:
    def __init__(self, *, scan_results=None, positions=None, sandbox=None,
                 tracker=None):
        self._scan_last_results = scan_results or {}
        self._position_tracker = tracker
        self._sandbox = sandbox


def test_owner_state_scanner_symbols_dedupes_across_scans():
    rows1 = [_StubMatchRow("AAPL"), _StubMatchRow("MSFT")]
    rows2 = [_StubMatchRow("MSFT"), _StubMatchRow("NVDA")]
    owner = _StubOwner(scan_results={
        "scan1": _StubScanResult(rows1),
        "scan2": _StubScanResult(rows2),
    })
    out = owner_state.scanner_symbols(owner)
    assert out == ["AAPL", "MSFT", "NVDA"]


def test_owner_state_scanner_symbols_respects_rank_value():
    rows = [
        _StubMatchRow("ZZZ", rank_value=10),
        _StubMatchRow("AAA", rank_value=1),
        _StubMatchRow("MMM", rank_value=5),
    ]
    owner = _StubOwner(scan_results={"scan1": _StubScanResult(rows)})
    out = owner_state.scanner_symbols(owner)
    assert out == ["AAA", "MMM", "ZZZ"]


def test_owner_state_scanner_row_for_returns_match():
    rows = [_StubMatchRow("AAPL", is_new=True), _StubMatchRow("MSFT")]
    owner = _StubOwner(scan_results={"scan1": _StubScanResult(rows)})
    row = owner_state.scanner_row_for(owner, "AAPL")
    assert row is rows[0]
    assert row.is_new is True


def test_owner_state_scanner_row_for_missing_returns_none():
    owner = _StubOwner(scan_results={})
    assert owner_state.scanner_row_for(owner, "AAPL") is None


def test_owner_state_open_positions_orders_by_abs_pnl():
    class _Pos:
        def __init__(self, sym, pnl):
            self.symbol = sym
            self.unrealized_pnl = pnl
            self.is_open = True

    class _Tracker:
        def list_open(self):
            return [_Pos("AAPL", 5.0), _Pos("MSFT", -20.0),
                    _Pos("NVDA", 1.0)]

    owner = _StubOwner(tracker=_Tracker())
    assert owner_state.open_position_symbols(owner) == ["MSFT", "AAPL", "NVDA"]


def test_owner_state_open_positions_prefers_sandbox():
    class _Sandbox:
        def is_active(self):
            return True

        def positions_snapshot(self):
            return [
                {"symbol": "TSLA", "unrealized_pnl": -10.0},
                {"symbol": "AAPL", "unrealized_pnl": 2.0},
            ]

    owner = _StubOwner(sandbox=_Sandbox())
    assert owner_state.open_position_symbols(owner) == ["TSLA", "AAPL"]


def test_owner_state_open_position_for_handles_both_shapes():
    class _Pos:
        def __init__(self, sym, sp):
            self.symbol = sym
            self.stop_price = sp
            self.is_open = True

    class _Tracker:
        def list_open(self):
            return [_Pos("AAPL", 99.7)]

    owner = _StubOwner(tracker=_Tracker())
    pos = owner_state.open_position_for(owner, "AAPL")
    assert getattr(pos, "stop_price", None) == 99.7
