"""PreTradeEntry default-safe save/load migration.

The proximity fields (next_earnings_ts, last_earnings_ts,
last_dividend_ts, last_split_ts, earnings_proximity_tag,
dividend_proximity_tag) are additive and must default to zero / empty
when loading a save file written before they existed. This locks in
the additive-only contract — bumping ENGINE_VERSION is the only
acceptable escape hatch.
"""
from __future__ import annotations

from tradinglab.backtest.journal import PreTradeEntry
from tradinglab.backtest.session import _pre_from_dict, _pre_to_dict


def _legacy_dict() -> dict:
    """Shape of a pre-feature save file."""
    return {
        "order_id": "ord-0001",
        "ts": 1_700_000_000,
        "symbol": "AAPL",
        "side": "buy",
        "setup_tag": "vwap_reclaim",
        "thesis": "thesis text",
        "conviction": 4,
        "size": 100.0,
        "target": 195.0,
        "notes": "",
    }


def test_pre_from_dict_legacy_save_loads_with_zero_proximity():
    p = _pre_from_dict(_legacy_dict())
    assert p.order_id == "ord-0001"
    assert p.next_earnings_ts == 0
    assert p.last_earnings_ts == 0
    assert p.last_dividend_ts == 0
    assert p.last_split_ts == 0
    assert p.earnings_proximity_tag == ""
    assert p.dividend_proximity_tag == ""


def test_pre_to_dict_emits_all_new_keys():
    p = PreTradeEntry(
        order_id="ord-0002", ts=1, symbol="MSFT", side="buy",
        setup_tag="t", thesis="x", conviction=3, size=50.0,
        next_earnings_ts=1_705_000_000_000,
        earnings_proximity_tag="earnings_pre_print",
    )
    d = _pre_to_dict(p)
    for key in ("next_earnings_ts", "last_earnings_ts",
                "last_dividend_ts", "last_split_ts",
                "earnings_proximity_tag", "dividend_proximity_tag"):
        assert key in d
    assert d["next_earnings_ts"] == 1_705_000_000_000
    assert d["earnings_proximity_tag"] == "earnings_pre_print"


def test_pre_round_trip_preserves_proximity_fields():
    original = PreTradeEntry(
        order_id="ord-0003", ts=2, symbol="TSLA", side="sell",
        setup_tag="reversal", thesis="x", conviction=2, size=10.0,
        target=200.0, notes="n",
        next_earnings_ts=1, last_earnings_ts=2, last_dividend_ts=3,
        last_split_ts=4,
        earnings_proximity_tag="earnings_post_print",
        dividend_proximity_tag="ex_div_day",
    )
    restored = _pre_from_dict(_pre_to_dict(original))
    assert restored == original
