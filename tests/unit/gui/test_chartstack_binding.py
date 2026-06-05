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


# ---------------------------------------------------------------------------
# FIXED_PRESET — per-slot fixed bindings (audit ``chartstack-fixed-preset``)
# ---------------------------------------------------------------------------


def test_fixed_preset_returns_slot_aligned_bindings() -> None:
    """FIXED_PRESET binds each slot to ``fixed_preset[i]`` directly,
    preserving order (no first-seen dedup, no source-priority cascade).
    The user-facing contract is: slot 0 = first symbol, slot 1 =
    second, slot 2 = third."""
    out = resolve_bindings(
        BindingMode.FIXED_PRESET,
        fixed_preset=["SPY", "QQQ", "VXX"],
        card_count=3,
    )
    assert out == [
        CardBinding("SPY", "preset"),
        CardBinding("QQQ", "preset"),
        CardBinding("VXX", "preset"),
    ]


def test_fixed_preset_normalises_lower_case() -> None:
    """Symbols are upper-cased on the way in, matching the other modes."""
    out = resolve_bindings(
        BindingMode.FIXED_PRESET,
        fixed_preset=["spy", "qqq", "vxx"],
        card_count=3,
    )
    assert [b.symbol for b in out if b is not None] == ["SPY", "QQQ", "VXX"]


def test_fixed_preset_pads_short_list_with_none() -> None:
    """Under-length preset → trailing slots are ``None`` (empty card).
    Differs from HYBRID which would fall through to other sources;
    FIXED_PRESET is deliberately literal."""
    out = resolve_bindings(
        BindingMode.FIXED_PRESET,
        fixed_preset=["SPY"],
        card_count=3,
    )
    assert out == [CardBinding("SPY", "preset"), None, None]


def test_fixed_preset_truncates_long_list() -> None:
    """Over-length preset → silently truncated to ``card_count``."""
    out = resolve_bindings(
        BindingMode.FIXED_PRESET,
        fixed_preset=["SPY", "QQQ", "VXX", "DIA", "IWM"],
        card_count=3,
    )
    assert [b.symbol for b in out if b is not None] == ["SPY", "QQQ", "VXX"]


def test_fixed_preset_ignores_blank_and_whitespace_slots() -> None:
    """Blank entries (``""``, ``"   "``) become ``None`` slots so the
    user can hold a slot empty in the popup without it eating later
    bindings."""
    out = resolve_bindings(
        BindingMode.FIXED_PRESET,
        fixed_preset=["SPY", "", "VXX"],
        card_count=3,
    )
    assert out == [
        CardBinding("SPY", "preset"),
        None,
        CardBinding("VXX", "preset"),
    ]


def test_fixed_preset_ignores_other_sources() -> None:
    """FIXED_PRESET deliberately does NOT fall through to watchlist /
    positions / scanner — the user's pinned list is the authoritative
    source. Otherwise the popup's "I want exactly these tickers" UX
    would silently leak the user's open positions into the cards."""
    out = resolve_bindings(
        BindingMode.FIXED_PRESET,
        fixed_preset=["SPY", "QQQ", "VXX"],
        watchlist=["AAPL"],
        scanner_results=["TSLA"],
        open_positions=["NVDA"],
        manual_pins=["MSFT"],
        card_count=3,
    )
    assert [b.symbol for b in out if b is not None] == ["SPY", "QQQ", "VXX"]


def test_fixed_preset_empty_yields_all_none() -> None:
    out = resolve_bindings(
        BindingMode.FIXED_PRESET,
        fixed_preset=(),
        card_count=3,
    )
    assert out == [None, None, None]


# ---------------------------------------------------------------------------
# Misc back-compat
# ---------------------------------------------------------------------------


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
