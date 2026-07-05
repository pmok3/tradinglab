"""Unit tests for the pure sandbox-heatmap layer (`backtest/heatmap.py`).

Headless: no Tk, no matplotlib. Pins the metric + geometry contract in
``backtest/heatmap.spec.md`` (squarify tiling, 1-Day %, historically-
scaled cap, sector→industry grouping, Finviz palette, point-in-time
membership, approx-size flagging).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradinglab.backtest import heatmap as H
from tradinglab.models import Candle

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _epoch(y, m, d) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def _candle(y, m, d, close, *, session="regular") -> Candle:
    dt = datetime(y, m, d, tzinfo=timezone.utc)
    return Candle(date=dt, open=close, high=close, low=close, close=close,
                  volume=100, session=session)


def _total_area(rects) -> float:
    return sum(w * h for (_x, _y, w, h) in rects)


# ---------------------------------------------------------------------------
# squarify
# ---------------------------------------------------------------------------


def test_squarify_tiles_parent_exactly():
    rects = H.squarify([5, 3, 2, 1, 1], 0.0, 0.0, 4.0, 3.0)
    assert len(rects) == 5
    assert _total_area(rects) == pytest.approx(12.0, rel=1e-9)


def test_squarify_no_negative_or_zero_dims_for_positive_values():
    rects = H.squarify([5, 3, 2, 1], 0.0, 0.0, 1.0, 1.0)
    for (x, y, w, h) in rects:
        assert w > 0.0 and h > 0.0
        assert x >= -1e-9 and y >= -1e-9
        assert x + w <= 1.0 + 1e-9
        assert y + h <= 1.0 + 1e-9


def test_squarify_deterministic():
    a = H.squarify([7, 5, 4, 3, 2, 1], 0.0, 0.0, 1.0, 2.0)
    b = H.squarify([7, 5, 4, 3, 2, 1], 0.0, 0.0, 1.0, 2.0)
    assert a == b


def test_squarify_single_and_empty():
    assert H.squarify([], 0, 0, 1, 1) == []
    one = H.squarify([9.0], 0.0, 0.0, 2.0, 3.0)
    assert len(one) == 1
    assert one[0] == pytest.approx((0.0, 0.0, 2.0, 3.0))


def test_squarify_order_matches_input():
    # Rectangles are returned in input order (caller maps back by index).
    rects = H.squarify([6, 4, 2], 0.0, 0.0, 1.0, 1.0)
    assert len(rects) == 3
    assert _total_area(rects) == pytest.approx(1.0, rel=1e-9)


# ---------------------------------------------------------------------------
# compute_1d_pct
# ---------------------------------------------------------------------------


def test_compute_1d_pct_basic():
    assert H.compute_1d_pct(110.0, 100.0) == pytest.approx(10.0)
    assert H.compute_1d_pct(95.0, 100.0) == pytest.approx(-5.0)


def test_compute_1d_pct_missing_or_zero():
    assert H.compute_1d_pct(None, 100.0) is None
    assert H.compute_1d_pct(100.0, None) is None
    assert H.compute_1d_pct(100.0, 0.0) is None
    assert H.compute_1d_pct(float("nan"), 100.0) is None
    assert H.compute_1d_pct(100.0, float("nan")) is None


# ---------------------------------------------------------------------------
# scaled_cap
# ---------------------------------------------------------------------------


def test_scaled_cap():
    assert H.scaled_cap(1_000_000, 50.0) == pytest.approx(50_000_000.0)
    assert H.scaled_cap(None, 50.0) == 0.0
    assert H.scaled_cap(1000, None) == 0.0
    assert H.scaled_cap(float("nan"), 50.0) == 0.0
    assert H.scaled_cap(-5, 50.0) == 0.0


# ---------------------------------------------------------------------------
# price_at_or_before  (no future leakage)
# ---------------------------------------------------------------------------


def test_price_at_or_before_no_future_leakage():
    candles = [
        _candle(2020, 1, 1, 10.0),
        _candle(2020, 1, 2, 11.0),
        _candle(2020, 1, 3, 12.0),  # after cutoff -> must be ignored
    ]
    cutoff = _epoch(2020, 1, 2)
    assert H.price_at_or_before(candles, cutoff) == pytest.approx(11.0)


def test_price_at_or_before_ms_normalization():
    candles = [_candle(2020, 1, 1, 10.0), _candle(2020, 1, 2, 11.0)]
    cutoff_s = _epoch(2020, 1, 2)
    cutoff_ms = cutoff_s * 1000
    assert H.price_at_or_before(candles, cutoff_ms) == pytest.approx(11.0)


def test_price_at_or_before_skips_nan_and_empty():
    candles = [
        _candle(2020, 1, 1, 10.0),
        Candle.gap(datetime(2020, 1, 2, tzinfo=timezone.utc)),  # NaN close
    ]
    # cutoff after the gap bar -> last real close is the 10.0 bar
    assert H.price_at_or_before(candles, _epoch(2020, 1, 3)) == pytest.approx(10.0)
    assert H.price_at_or_before([], _epoch(2020, 1, 1)) is None
    # cutoff before any bar -> None
    assert H.price_at_or_before(candles, _epoch(2019, 1, 1)) is None


# ---------------------------------------------------------------------------
# members_asof  (point-in-time membership)
# ---------------------------------------------------------------------------


def test_members_asof_excludes_lookahead_inclusive_boundary():
    as_of = _epoch(2020, 1, 1)
    dates = {
        "OLD": _epoch(2010, 6, 1),   # added before -> in
        "BOUND": _epoch(2020, 1, 1),  # added exactly at clock -> in (inclusive)
        "NEW": _epoch(2022, 3, 1),   # added after -> out (look-ahead)
        "UNK": None,                  # unknown date -> in
    }
    members = H.members_asof(dates, as_of)
    assert "OLD" in members
    assert "BOUND" in members
    assert "UNK" in members
    assert "NEW" not in members


def test_members_asof_preserves_order():
    as_of = _epoch(2025, 1, 1)
    dates = {"A": _epoch(2000, 1, 1), "B": _epoch(2001, 1, 1), "C": _epoch(2002, 1, 1)}
    assert H.members_asof(dates, as_of) == ("A", "B", "C")


# ---------------------------------------------------------------------------
# finviz_hex  (color)
# ---------------------------------------------------------------------------


def test_finviz_hex_neutral_and_none():
    assert H.finviz_hex(0.0) == H._NEUTRAL_HEX
    assert H.finviz_hex(None) == H._NEUTRAL_HEX
    assert H.finviz_hex(float("nan")) == H._NEUTRAL_HEX
    # small move inside the neutral band
    assert H.finviz_hex(0.2) == H._NEUTRAL_HEX


def test_finviz_hex_extremes_and_clip():
    assert H.finviz_hex(3.0) == H._GREEN_HEX
    assert H.finviz_hex(-3.0) == H._RED_HEX
    # beyond the clip saturates to the same extreme
    assert H.finviz_hex(9.9, clip_pct=3.0) == H._GREEN_HEX
    assert H.finviz_hex(-9.9, clip_pct=3.0) == H._RED_HEX


def test_finviz_hex_intermediate_bucket_between_neutral_and_green():
    c = H.finviz_hex(1.0)  # +1% -> one green step
    assert c not in (H._NEUTRAL_HEX, H._GREEN_HEX, H._RED_HEX)


# ---------------------------------------------------------------------------
# luminance / text color
# ---------------------------------------------------------------------------


def test_text_color_contrast():
    assert H.text_color_for("#ffffff") == "#000000"
    assert H.text_color_for("#000000") == "#ffffff"
    assert H.text_color_for(H._NEUTRAL_HEX) == "#ffffff"
    assert 0.0 <= H.relative_luminance("#808080") <= 1.0


# ---------------------------------------------------------------------------
# build_layout  (grouping + geometry + approx flag)
# ---------------------------------------------------------------------------


def _sample_inputs():
    symbols = ["AAA", "BBB", "CCC", "DDD", "ZZZ"]
    classification = {
        "AAA": H.Classification("Technology", "Software"),
        "BBB": H.Classification("Technology", "Semiconductors"),
        "CCC": H.Classification("Technology", "Software"),
        "DDD": H.Classification("Financials", "Banks"),
        # ZZZ intentionally missing -> Unclassified
    }
    size_by_symbol = {"AAA": 3e12, "BBB": 1e12, "CCC": 5e11, "DDD": 2e12, "ZZZ": 0.0}
    return symbols, classification, size_by_symbol


def test_build_layout_every_symbol_once_and_in_unit_square():
    symbols, cls, sizes = _sample_inputs()
    layout = H.build_layout(symbols=symbols, size_by_symbol=sizes, classification=cls)
    assert len(layout.tiles) == len(symbols)
    assert {t.symbol for t in layout.tiles} == set(symbols)
    for t in layout.tiles:
        assert t.x >= -1e-9 and t.y >= -1e-9
        assert t.x + t.w <= 1.0 + 1e-9
        assert t.y + t.h <= 1.0 + 1e-9
        assert t.w > 0.0 and t.h > 0.0


def test_build_layout_grouping_and_unclassified():
    symbols, cls, sizes = _sample_inputs()
    layout = H.build_layout(symbols=symbols, size_by_symbol=sizes, classification=cls)
    by_sym = {t.symbol: t for t in layout.tiles}
    assert by_sym["AAA"].sector == "Technology"
    assert by_sym["AAA"].industry == "Software"
    assert by_sym["ZZZ"].sector == H.UNCLASSIFIED
    assert by_sym["ZZZ"].industry == H.UNCLASSIFIED
    assert "Technology" in layout.sector_bounds
    assert ("Technology", "Software") in layout.industry_bounds


def test_build_layout_approx_size_flag_only_for_flagged():
    symbols, cls, sizes = _sample_inputs()
    layout = H.build_layout(
        symbols=symbols, size_by_symbol=sizes, classification=cls,
        approx_size_symbols={"CCC"},
    )
    by_sym = {t.symbol: t for t in layout.tiles}
    assert by_sym["CCC"].approx_size is True
    assert all(not t.approx_size for t in layout.tiles if t.symbol != "CCC")


def test_build_layout_tiles_cover_unit_square():
    symbols, cls, sizes = _sample_inputs()
    layout = H.build_layout(symbols=symbols, size_by_symbol=sizes, classification=cls)
    total = sum(t.w * t.h for t in layout.tiles)
    assert total == pytest.approx(1.0, rel=1e-6)


# ---------------------------------------------------------------------------
# apply_colors  (non-mutation + model)
# ---------------------------------------------------------------------------


def test_apply_colors_non_mutation_and_fill():
    symbols, cls, sizes = _sample_inputs()
    layout = H.build_layout(symbols=symbols, size_by_symbol=sizes, classification=cls)
    original_tiles = layout.tiles  # identity + values must survive
    pcts = {"AAA": 2.5, "BBB": -3.0, "CCC": None, "DDD": 0.0}  # ZZZ missing
    model = H.apply_colors(
        layout, pct_by_symbol=pcts, as_of_ts=_epoch(2020, 6, 1),
        clip_pct=3.0, timeframe="1D", universe_id="sp500",
    )
    # layout untouched
    assert layout.tiles is original_tiles
    assert all(t.pct is None and t.fill == "" for t in layout.tiles)
    # model colored
    by_sym = {t.symbol: t for t in model.tiles}
    assert by_sym["AAA"].pct == pytest.approx(2.5)
    assert by_sym["BBB"].fill == H._RED_HEX
    # missing / None pct -> neutral, never an extreme
    assert by_sym["CCC"].fill == H._NEUTRAL_HEX
    assert by_sym["ZZZ"].fill == H._NEUTRAL_HEX
    assert by_sym["DDD"].fill == H._NEUTRAL_HEX
    assert model.timeframe == "1D"
    assert model.universe_id == "sp500"
    assert model.clip_pct == 3.0
    assert model.as_of_ts == _epoch(2020, 6, 1)
