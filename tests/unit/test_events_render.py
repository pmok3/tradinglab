"""Events render-descriptor builder tests.

Locks in:
* past dividends + earnings produce in-pane glyphs anchored by date
* forward-only earnings produce a right-edge badge (bar_index = -1)
* blind mode emits a relative tooltip ("T-N") not an absolute date
* splits produce GLYPH_SPLIT, specials produce GLYPH_SPECIAL_DIVIDEND
* events whose calendar day doesn't appear in the visible window are
  dropped (no spurious in-pane glyphs)
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass

from tradinglab.events.base import DividendRecord, EarningsRecord
from tradinglab.events.gating import EventsView, ForwardEarningsBadge
from tradinglab.events.render import (
    GLYPH_DIVIDEND,
    GLYPH_EARNINGS_FORWARD,
    GLYPH_EARNINGS_PAST,
    GLYPH_SPECIAL_DIVIDEND,
    GLYPH_SPLIT,
    build_event_glyphs,
)


@dataclass
class _Candle:
    """Minimal candle with a ``date`` attribute, as the renderer expects."""
    date: _dt.datetime


def _day(y: int, m: int, d: int) -> _dt.datetime:
    return _dt.datetime(y, m, d, tzinfo=_dt.timezone.utc)


def _ms(y: int, m: int, d: int) -> int:
    return int(_day(y, m, d).timestamp() * 1000)


def _candles_jan() -> list:
    return [_Candle(date=_day(2024, 1, i)) for i in range(2, 12)]


def test_past_earnings_anchors_to_matching_bar():
    candles = _candles_jan()
    view = EventsView(past_earnings=[
        EarningsRecord(ts=_ms(2024, 1, 5), symbol="X", when="AMC",
                       eps_estimate=1.0, eps_actual=1.1,
                       revenue_estimate=math.nan,
                       revenue_actual=math.nan,
                       source="test"),
    ])
    glyphs = build_event_glyphs(view, candles, blind=False)
    assert len(glyphs) == 1
    g = glyphs[0]
    assert g.glyph_kind == GLYPH_EARNINGS_PAST
    assert candles[g.bar_index].date.day == 5
    assert "EPS" in g.tooltip


def test_dividends_emit_correct_glyph_kinds():
    candles = _candles_jan()
    view = EventsView(past_dividends=[
        DividendRecord(ex_ts=_ms(2024, 1, 3), symbol="X",
                       amount=0.25, kind="cash", source="t"),
        DividendRecord(ex_ts=_ms(2024, 1, 7), symbol="X",
                       amount=2.00, kind="special", source="t"),
        DividendRecord(ex_ts=_ms(2024, 1, 9), symbol="X",
                       amount=0.0, kind="stock_split",
                       ratio_num=2, ratio_den=1, source="t"),
    ])
    glyphs = build_event_glyphs(view, candles, blind=False)
    kinds = {g.glyph_kind for g in glyphs}
    assert GLYPH_DIVIDEND in kinds
    assert GLYPH_SPECIAL_DIVIDEND in kinds
    assert GLYPH_SPLIT in kinds


def test_event_outside_visible_window_is_dropped():
    candles = _candles_jan()
    view = EventsView(past_earnings=[
        # 2023-12-15 — earlier than any candle in the window.
        EarningsRecord(ts=_ms(2023, 12, 15), symbol="X", when="AMC"),
    ])
    glyphs = build_event_glyphs(view, candles, blind=False)
    assert glyphs == []


def test_forward_badge_in_blind_mode_uses_relative_tooltip():
    candles = _candles_jan()
    view = EventsView(forward_badges=[
        ForwardEarningsBadge(trading_days_until=4, when="BMO"),
    ])
    glyphs = build_event_glyphs(view, candles, blind=True)
    assert len(glyphs) == 1
    g = glyphs[0]
    assert g.bar_index == -1
    assert g.glyph_kind == GLYPH_EARNINGS_FORWARD
    assert "T-4" in g.tooltip
    # No absolute date leakage
    assert "2024" not in g.tooltip


def test_forward_in_pane_glyph_suppresses_right_edge_badge():
    candles = _candles_jan()
    # Forward earnings whose ts lands inside the visible window —
    # in-pane glyph wins, the right-edge badge is suppressed.
    view = EventsView(
        forward_earnings=[
            EarningsRecord(ts=_ms(2024, 1, 8), symbol="X",
                           when="AMC", eps_estimate=2.0),
        ],
        forward_badges=[
            ForwardEarningsBadge(trading_days_until=4, when="AMC"),
        ],
    )
    glyphs = build_event_glyphs(view, candles, blind=False)
    assert len(glyphs) == 1
    assert glyphs[0].bar_index >= 0
    assert glyphs[0].glyph_kind == GLYPH_EARNINGS_FORWARD


def test_none_view_returns_empty():
    assert build_event_glyphs(None, _candles_jan()) == []
