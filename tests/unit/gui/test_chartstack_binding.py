"""Unit tests for :mod:`tradinglab.gui.chartstack.binding`."""

from __future__ import annotations

from tradinglab.gui.chartstack.binding import (
    BindingMode,
    CardBinding,
    resolve_bindings,
)


def test_pinned_watchlist_basic() -> None:
    out = resolve_bindings(
        BindingMode.PINNED_WATCHLIST,
        watchlist=["AAPL", "MSFT", "NVDA"],
        card_count=3,
    )
    assert out == [
        CardBinding("AAPL", "watchlist"),
        CardBinding("MSFT", "watchlist"),
        CardBinding("NVDA", "watchlist"),
    ]


def test_scanner_top_n() -> None:
    out = resolve_bindings(
        BindingMode.SCANNER_TOP_N,
        scanner_results=["TSLA", "AMD"],
        card_count=3,
    )
    assert out[0] == CardBinding("TSLA", "scanner")
    assert out[1] == CardBinding("AMD", "scanner")
    assert out[2] is None  # padded


def test_open_positions_dedup() -> None:
    out = resolve_bindings(
        BindingMode.OPEN_POSITIONS,
        open_positions=["aapl", "AAPL", "msft"],
        card_count=3,
    )
    syms = [b.symbol for b in out if b is not None]
    assert syms == ["AAPL", "MSFT"]


def test_pad_with_none_when_under_count() -> None:
    out = resolve_bindings(
        BindingMode.PINNED_WATCHLIST,
        watchlist=["AAPL"],
        card_count=4,
    )
    assert len(out) == 4
    assert out[0] == CardBinding("AAPL", "watchlist")
    assert out[1] is None and out[2] is None and out[3] is None


def test_truncate_when_over_count() -> None:
    out = resolve_bindings(
        BindingMode.PINNED_WATCHLIST,
        watchlist=["A", "B", "C", "D", "E"],
        card_count=2,
    )
    assert [b.symbol for b in out] == ["A", "B"]


def test_hybrid_ordering_positions_first() -> None:
    out = resolve_bindings(
        BindingMode.HYBRID,
        watchlist=["AAPL", "MSFT"],
        scanner_results=["TSLA"],
        open_positions=["NVDA"],
        manual_pins=["GOOGL"],
        card_count=5,
    )
    syms = [b.symbol for b in out]
    assert syms == ["NVDA", "GOOGL", "AAPL", "MSFT", "TSLA"]
    labels = [b.source_label for b in out]
    assert labels == ["position", "pinned", "watchlist", "watchlist", "scanner"]


def test_hybrid_dedup_across_sources() -> None:
    out = resolve_bindings(
        BindingMode.HYBRID,
        watchlist=["AAPL", "NVDA"],
        scanner_results=["AAPL"],
        open_positions=["NVDA"],
        manual_pins=["AAPL"],
        card_count=4,
    )
    syms = [b.symbol if b else None for b in out]
    assert syms == ["NVDA", "AAPL", None, None]
    # NVDA should be labeled "position" (first source it appeared in)
    assert out[0].source_label == "position"
    # AAPL should be labeled "pinned" (positions skipped, pinned beat watchlist)
    assert out[1].source_label == "pinned"


def test_hybrid_caps_at_card_count() -> None:
    out = resolve_bindings(
        BindingMode.HYBRID,
        watchlist=["A", "B", "C", "D", "E"],
        open_positions=["F", "G"],
        card_count=3,
    )
    syms = [b.symbol for b in out]
    assert syms == ["F", "G", "A"]


def test_zero_card_count_returns_empty_list() -> None:
    assert resolve_bindings(BindingMode.HYBRID, card_count=0) == []


def test_input_tolerance_dicts_and_objects() -> None:
    class _Row:
        def __init__(self, t: str) -> None:
            self.ticker = t

    out = resolve_bindings(
        BindingMode.PINNED_WATCHLIST,
        watchlist=[{"symbol": "aapl"}, _Row("MSFT"), "  nvda  "],
        card_count=3,
    )
    assert [b.symbol for b in out] == ["AAPL", "MSFT", "NVDA"]
