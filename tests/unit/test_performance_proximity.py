"""Performance proximity-aggregator tests.

Locks in:
* trades with no proximity tags fall into the empty-string bucket
* a trade with both tags contributes to two rows
* sort order is descending count then alphabetical
* win/loss math matches build_setup_aggregates conventions
"""
from __future__ import annotations

from tradinglab.backtest.journal import PostTradeReview, PreTradeEntry
from tradinglab.backtest.performance import (
    TradeRow,
    build_proximity_aggregates,
)


def _row(
    pnl: float,
    *,
    earnings_tag: str = "",
    dividend_tag: str = "",
    order_id: str = "o1",
) -> TradeRow:
    post = PostTradeReview(
        symbol="X",
        entry_ts=1, exit_ts=2,
        entry_price=10.0, exit_price=10.0 + pnl / 100.0,
        quantity=100.0,
        side="buy",
        pnl=pnl,
        pnl_pct=0.0,
        mae=0.0, mfe=0.0,
        mae_pct=0.0, mfe_pct=0.0,
        ref_pre_trade_id=order_id,
    )
    pre = PreTradeEntry(
        order_id=order_id, ts=1, symbol="X", side="buy",
        setup_tag="t", thesis="x", conviction=3, size=100.0,
        earnings_proximity_tag=earnings_tag,
        dividend_proximity_tag=dividend_tag,
    )
    return TradeRow(post=post, pre=pre)


def test_no_proximity_tags_falls_into_empty_bucket():
    rows = [_row(50.0), _row(-25.0)]
    aggs = build_proximity_aggregates(rows)
    assert len(aggs) == 1
    assert aggs[0].proximity_tag == ""
    assert aggs[0].count == 2
    assert aggs[0].wins == 1
    assert aggs[0].losses == 1
    assert aggs[0].total_pnl == 25.0


def test_dual_tag_trade_contributes_to_two_rows():
    rows = [
        _row(100.0, earnings_tag="earnings_pre_print",
             dividend_tag="ex_div_day", order_id="dual"),
    ]
    aggs = build_proximity_aggregates(rows)
    tags = {a.proximity_tag for a in aggs}
    assert tags == {"earnings_pre_print", "ex_div_day"}
    for a in aggs:
        assert a.count == 1
        assert a.wins == 1
        assert a.total_pnl == 100.0


def test_sort_order_descending_count_then_alpha():
    rows = [
        _row(10.0, earnings_tag="earnings_pre_print", order_id="a"),
        _row(20.0, earnings_tag="earnings_pre_print", order_id="b"),
        _row(30.0, dividend_tag="ex_div_day", order_id="c"),
    ]
    aggs = build_proximity_aggregates(rows)
    assert aggs[0].proximity_tag == "earnings_pre_print"
    assert aggs[0].count == 2
    assert aggs[1].proximity_tag == "ex_div_day"
    assert aggs[1].count == 1
