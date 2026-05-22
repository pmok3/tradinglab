"""EntriesOverlay tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import pytest
from matplotlib.figure import Figure

from tradinglab.core import thread_guard
from tradinglab.entries.evaluator import EntryEvaluator
from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    SizingKind,
    SizingRule,
    TriggerKind,
    Universe,
)
from tradinglab.entries.signals import EntryPaperSink
from tradinglab.exits.model import OrderSide
from tradinglab.exits.paper_engine import (
    OrderTargetKind,
    PaperBrokerEngine,
    PaperOrder,
    PaperOrderKind,
)
from tradinglab.gui.entries_overlay import (
    EntriesOverlay,
    OverlayLine,
    compute_overlay_lines,
)
from tradinglab.positions.tracker import PositionTracker


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


def _strategy(
    *,
    name: str = "s",
    direction: Direction = Direction.LONG,
    trigger_kind: TriggerKind = TriggerKind.LIMIT,
    price=None,
    stop_price=None,
    symbols=("AAPL",),
    enabled: bool = True,
) -> EntryStrategy:
    return EntryStrategy(
        name=name,
        direction=direction,
        universe=Universe(symbols=tuple(symbols)),
        trigger=EntryTrigger(kind=trigger_kind, price=price,
                             stop_price=stop_price),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100.0),
        enabled=enabled,
    )


def _evaluator():
    tracker = PositionTracker()
    engine = PaperBrokerEngine(tracker)
    sink = EntryPaperSink(engine)
    return (EntryEvaluator(tracker=tracker, sink=sink), tracker, engine)


def _ax():
    fig = Figure()
    return fig.add_subplot(1, 1, 1)


# ---------------------------------------------------------------------------
# compute_overlay_lines (pure)
# ---------------------------------------------------------------------------


def test_no_lines_without_armed_or_pending():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0)
    ev.set_strategies([s])
    # Not armed → no line.
    out = compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL")
    assert out == []


def test_armed_limit_long_renders_green_dashed():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    out = compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL")
    assert len(out) == 1
    line = out[0]
    assert line.kind == "armed_limit"
    assert line.price == pytest.approx(150.0)
    assert line.color == "#28a745"
    assert line.linestyle == "--"
    assert "ARMED LIMIT" in line.label


def test_armed_limit_short_renders_red():
    ev, _t, eng = _evaluator()
    s = _strategy(direction=Direction.SHORT, price=200.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    out = compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL")
    assert out[0].color == "#d73a49"


def test_armed_stop_renders_dotted():
    ev, _t, eng = _evaluator()
    s = _strategy(trigger_kind=TriggerKind.STOP, stop_price=140.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    out = compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL")
    assert out[0].kind == "armed_stop"
    assert out[0].linestyle == ":"
    assert out[0].price == pytest.approx(140.0)


def test_armed_stop_limit_renders_dotted():
    ev, _t, eng = _evaluator()
    s = _strategy(trigger_kind=TriggerKind.STOP_LIMIT, stop_price=140.0,
                  price=141.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    out = compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL")
    assert out[0].kind == "armed_stop_limit"
    assert out[0].price == pytest.approx(140.0)


def test_market_trigger_no_line():
    ev, _t, eng = _evaluator()
    s = _strategy(trigger_kind=TriggerKind.MARKET)
    ev.set_strategies([s])
    ev.arm(s.id)
    assert compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL") == []


def test_indicator_trigger_no_line():
    ev, _t, eng = _evaluator()
    s = _strategy(trigger_kind=TriggerKind.MARKET)
    ev.set_strategies([s])
    ev.arm(s.id)
    # Mutate trigger kind post-arm to INDICATOR (skips validation that
    # arm() would have caught) — overlay must still skip it.
    s.trigger.kind = TriggerKind.INDICATOR
    assert compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL") == []


def test_scanner_alert_trigger_no_line():
    ev, _t, eng = _evaluator()
    s = _strategy(trigger_kind=TriggerKind.MARKET)
    ev.set_strategies([s])
    ev.arm(s.id)
    s.trigger.kind = TriggerKind.SCANNER_ALERT
    assert compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL") == []


def test_disabled_strategy_skipped():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0, enabled=True)
    ev.set_strategies([s])
    ev.arm(s.id)
    # Disable AFTER arming — overlay should skip disabled ones even if
    # the runtime arm bit is still set.
    s.enabled = False
    assert compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL") == []


def test_universe_filter_excludes_other_symbols():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0, symbols=("MSFT",))
    ev.set_strategies([s])
    ev.arm(s.id)
    assert compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL") == []


def test_no_primary_symbol_yields_no_lines():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    assert compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol=None) == []
    assert compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="") == []


def test_pending_limit_order_renders_solid():
    ev, _t, eng = _evaluator()
    po = PaperOrder(
        id="po-1", position_id="", kind=PaperOrderKind.LIMIT,
        side=OrderSide.BUY, qty=10.0, price=149.0,
        target_kind=OrderTargetKind.PENDING_ENTRY,
        symbol="AAPL", pending_position_id="pp-1",
        position_side="long",
        strategy_id="strat-1",
    )
    eng._working["po-1"] = po; eng._pending_by_symbol.setdefault("AAPL", []).append("po-1")  # type: ignore[attr-defined]
    out = compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL")
    assert len(out) == 1
    line = out[0]
    assert line.pending is True
    assert line.linestyle == "-"
    assert "PENDING LIMIT" in line.label
    assert line.color == "#28a745"


def test_pending_stop_limit_yields_two_lines():
    ev, _t, eng = _evaluator()
    po = PaperOrder(
        id="po-2", position_id="", kind=PaperOrderKind.STOP_LIMIT,
        side=OrderSide.SELL, qty=20.0, price=140.0, limit_price=139.5,
        target_kind=OrderTargetKind.PENDING_ENTRY,
        symbol="AAPL", pending_position_id="pp-2",
        position_side="short",
        strategy_id="strat-2",
    )
    eng._working["po-2"] = po; eng._pending_by_symbol.setdefault("AAPL", []).append("po-2")  # type: ignore[attr-defined]
    out = compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL")
    assert len(out) == 2
    kinds = [o.kind for o in out]
    assert "pending_stop_limit_stop" in kinds
    assert "pending_stop_limit_limit" in kinds
    # SHORT side → red.
    assert all(o.color == "#d73a49" for o in out)


def test_armed_and_pending_combined():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    po = PaperOrder(
        id="po-3", position_id="", kind=PaperOrderKind.STOP,
        side=OrderSide.BUY, qty=10.0, price=145.0,
        target_kind=OrderTargetKind.PENDING_ENTRY,
        symbol="AAPL", pending_position_id="pp-3",
        position_side="long",
        strategy_id=s.id,
    )
    eng._working["po-3"] = po; eng._pending_by_symbol.setdefault("AAPL", []).append("po-3")  # type: ignore[attr-defined]
    out = compute_overlay_lines(
        evaluator=ev, paper_engine=eng, primary_symbol="AAPL")
    assert len(out) == 2
    kinds = [o.kind for o in out]
    assert "armed_limit" in kinds
    assert "pending_stop" in kinds


# ---------------------------------------------------------------------------
# EntriesOverlay (renderer)
# ---------------------------------------------------------------------------


def test_renderer_with_no_state_is_noop():
    ev, _t, eng = _evaluator()
    overlay = EntriesOverlay(evaluator=ev, paper_engine=eng)
    out = overlay.redraw(_ax(), "AAPL")
    assert out == []
    assert overlay.line_count == 0


def test_renderer_draws_armed_line():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    overlay = EntriesOverlay(evaluator=ev, paper_engine=eng)
    out = overlay.redraw(_ax(), "AAPL")
    assert len(out) == 1
    assert overlay.line_count == 1


def test_renderer_disabled_no_lines():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    overlay = EntriesOverlay(evaluator=ev, paper_engine=eng, enabled=False)
    out = overlay.redraw(_ax(), "AAPL")
    assert out == []


def test_renderer_clear_drops_artist_refs():
    ev, _t, eng = _evaluator()
    s = _strategy(price=150.0)
    ev.set_strategies([s])
    ev.arm(s.id)
    overlay = EntriesOverlay(evaluator=ev, paper_engine=eng)
    overlay.redraw(_ax(), "AAPL")
    assert overlay.line_count == 1
    overlay.clear()
    assert overlay.line_count == 0


def test_renderer_set_enabled_triggers_redraw_callback():
    ev, _t, eng = _evaluator()
    fired: List[bool] = []
    overlay = EntriesOverlay(
        evaluator=ev, paper_engine=eng,
        request_redraw=lambda: fired.append(True),
    )
    overlay.set_enabled(False)
    assert fired == [True]
    overlay.set_enabled(False)  # idempotent
    assert len(fired) == 1

