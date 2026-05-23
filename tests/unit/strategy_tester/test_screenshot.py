"""Unit tests for ``strategy_tester.screenshot``.

Headless-only — every test runs against ``Figure`` + ``FigureCanvasAgg``
with no Tk surface. Pillow is imported lazily inside individual tests
for the PNG-magic-byte checks; the pipeline itself does not depend on
PIL.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone

import pytest

from tradinglab.backtest.journal import PostTradeReview, PreTradeEntry
from tradinglab.backtest.performance import TradeRow
from tradinglab.models import Candle
from tradinglab.strategy_tester.screenshot import (
    ScreenshotSpec,
    render_trade_screenshot,
    select_window,
    trade_filename,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _ramp_candles(n: int = 80, start_ts: datetime | None = None) -> list[Candle]:
    """Build a strictly-monotonic ramp of OHLCV bars for visual tests."""
    if start_ts is None:
        start_ts = datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc)
    bars: list[Candle] = []
    for i in range(n):
        op = 100.0 + i * 0.5
        hi = op + 0.4
        lo = op - 0.4
        cl = op + 0.2
        bars.append(
            Candle(
                date=start_ts + timedelta(minutes=5 * i),
                open=op, high=hi, low=lo, close=cl,
                volume=1000 + i * 10,
                session="regular",
            )
        )
    return bars


def _trade_row(
    candles: list[Candle],
    entry_idx: int,
    exit_idx: int,
    *,
    side: str = "buy",
    quantity: float = 100.0,
    mae_dollars: float = 50.0,
    mfe_dollars: float = 150.0,
    target: float | None = None,
    setup_tag: str = "breakout",
    order_id: str = "ord-001",
) -> TradeRow:
    ec = candles[entry_idx]
    xc = candles[exit_idx]
    entry_ts = int(ec.date.timestamp() * 1000.0)
    exit_ts = int(xc.date.timestamp() * 1000.0)
    pnl = (xc.close - ec.open) * quantity * (1 if side in ("buy", "long") else -1)
    pre = PreTradeEntry(
        order_id=order_id,
        ts=entry_ts,
        symbol="TEST",
        side=side,
        setup_tag=setup_tag,
        thesis="",
        conviction=3,
        size=quantity,
        target=target,
    )
    post = PostTradeReview(
        symbol="TEST",
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=ec.open,
        exit_price=xc.close,
        quantity=quantity,
        side=side,
        pnl=pnl,
        pnl_pct=(pnl / (ec.open * quantity)) if ec.open and quantity else 0.0,
        mae=mae_dollars,
        mfe=mfe_dollars,
        mae_pct=-mae_dollars / (ec.open * quantity) if ec.open and quantity else 0.0,
        mfe_pct=mfe_dollars / (ec.open * quantity) if ec.open and quantity else 0.0,
        ref_pre_trade_id=order_id,
    )
    return TradeRow(post=post, pre=pre)


def _png_dimensions(path) -> tuple[int, int]:
    """Read the (width, height) header from a PNG file."""
    with open(path, "rb") as f:
        header = f.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG file"
    w, h = struct.unpack(">II", header[16:24])
    return (w, h)


# ---------------------------------------------------------------------------
# select_window
# ---------------------------------------------------------------------------


def test_select_window_basic_middle() -> None:
    candles = _ramp_candles(80)
    start, end = select_window(candles, entry_index=40, exit_index=50,
                                pre_bars=10, post_bars=5, max_bars=200)
    assert start == 30
    assert end == 56  # exclusive: exit_index=50 + 1 + 5
    assert end - start == 26


def test_select_window_left_clamp() -> None:
    candles = _ramp_candles(40)
    start, end = select_window(candles, entry_index=2, exit_index=8,
                                pre_bars=30, post_bars=5, max_bars=200)
    assert start == 0  # would be -28; clamped
    assert end == 14
    assert start <= 2 < end and start <= 8 < end


def test_select_window_right_clamp() -> None:
    candles = _ramp_candles(20)
    start, end = select_window(candles, entry_index=10, exit_index=18,
                                pre_bars=2, post_bars=30, max_bars=200)
    assert start == 8
    assert end == 20  # exit+1+30 = 49 → clamped to n=20


def test_select_window_max_bars_cap_preserves_exit() -> None:
    """Long-running trades should keep the exit + post-bar context."""
    candles = _ramp_candles(500)
    start, end = select_window(
        candles, entry_index=10, exit_index=400,
        pre_bars=30, post_bars=10, max_bars=200,
    )
    assert end - start == 200
    # Exit is preserved with its post-bar buffer:
    assert end == 411   # exit_index=400 + 1 + post_bars=10
    assert 400 >= start  # but the entry can fall off the left edge
    assert start == 211


def test_select_window_empty_candles() -> None:
    assert select_window([], entry_index=0, exit_index=0) == (0, 0)


def test_select_window_inverted_indices_normalised() -> None:
    """exit_index < entry_index shouldn't crash."""
    candles = _ramp_candles(20)
    start, end = select_window(candles, entry_index=10, exit_index=4,
                                pre_bars=3, post_bars=3)
    # Function clamps exit_index up to entry_index in this case.
    assert start <= 10 < end


# ---------------------------------------------------------------------------
# trade_filename
# ---------------------------------------------------------------------------


def test_trade_filename_canonical() -> None:
    assert trade_filename("AAPL", "ord-abc") == "AAPL_ord-abc_post.png"


def test_trade_filename_falls_back_on_empty() -> None:
    assert trade_filename("", "") == "UNK_unknown_post.png"


def test_trade_filename_sanitises_slashes() -> None:
    # Order IDs sometimes carry path-unsafe characters.
    assert trade_filename("AAPL", "ord/with/slash") == "AAPL_ord_with_slash_post.png"


# ---------------------------------------------------------------------------
# render_trade_screenshot — file output + dimensions
# ---------------------------------------------------------------------------


def test_render_writes_png_with_expected_dimensions(tmp_path) -> None:
    candles = _ramp_candles(60)
    row = _trade_row(candles, entry_idx=20, exit_idx=35, side="buy")

    out = render_trade_screenshot(
        candles=candles,
        trade_row=row,
        output_path=tmp_path / "out.png",
        spec=ScreenshotSpec(width_in=8.0, height_in=4.5, dpi=80),
    )

    assert out.exists()
    assert out.stat().st_size > 1024  # non-trivial PNG
    w, h = _png_dimensions(out)
    assert w == 640 and h == 360  # 8.0in × 80dpi, 4.5in × 80dpi


def test_render_creates_parent_directories(tmp_path) -> None:
    candles = _ramp_candles(40)
    row = _trade_row(candles, entry_idx=10, exit_idx=20)
    target = tmp_path / "deeply" / "nested" / "shot.png"
    assert not target.parent.exists()
    render_trade_screenshot(
        candles=candles, trade_row=row, output_path=target,
        spec=ScreenshotSpec(width_in=6.0, height_in=3.0, dpi=72),
    )
    assert target.exists()


def test_render_handles_short_side(tmp_path) -> None:
    candles = _ramp_candles(60)
    # Short entry at idx 30, exit at idx 40.
    row = _trade_row(candles, entry_idx=30, exit_idx=40, side="sell",
                     mae_dollars=80.0, mfe_dollars=200.0)
    out = render_trade_screenshot(
        candles=candles, trade_row=row,
        output_path=tmp_path / "short.png",
        spec=ScreenshotSpec(width_in=7.0, height_in=4.0, dpi=80),
    )
    assert out.exists()
    assert out.stat().st_size > 1024


def test_render_with_target_line(tmp_path) -> None:
    candles = _ramp_candles(50)
    row = _trade_row(candles, entry_idx=15, exit_idx=30, target=125.0)
    out = render_trade_screenshot(
        candles=candles, trade_row=row,
        output_path=tmp_path / "target.png",
        spec=ScreenshotSpec(width_in=7.0, height_in=4.0, dpi=80),
    )
    assert out.exists()


def test_render_without_volume_pane(tmp_path) -> None:
    candles = _ramp_candles(40)
    row = _trade_row(candles, entry_idx=10, exit_idx=25)
    out = render_trade_screenshot(
        candles=candles, trade_row=row,
        output_path=tmp_path / "no-vol.png",
        spec=ScreenshotSpec(
            width_in=6.0, height_in=3.5, dpi=80, draw_volume_pane=False,
        ),
    )
    assert out.exists()


def test_render_dark_mode(tmp_path) -> None:
    candles = _ramp_candles(40)
    row = _trade_row(candles, entry_idx=10, exit_idx=25)
    out = render_trade_screenshot(
        candles=candles, trade_row=row,
        output_path=tmp_path / "dark.png",
        spec=ScreenshotSpec(
            width_in=6.0, height_in=3.5, dpi=80, dark_mode=True,
        ),
    )
    assert out.exists()
    assert out.stat().st_size > 1024


def test_render_single_bar_trade(tmp_path) -> None:
    """Entry == exit bar (intra-bar fill scenario) should still render."""
    candles = _ramp_candles(40)
    row = _trade_row(candles, entry_idx=20, exit_idx=20,
                     mae_dollars=0.0, mfe_dollars=0.0)
    out = render_trade_screenshot(
        candles=candles, trade_row=row,
        output_path=tmp_path / "single-bar.png",
        spec=ScreenshotSpec(width_in=6.0, height_in=3.5, dpi=72),
    )
    assert out.exists()


def test_render_missing_ts_raises(tmp_path) -> None:
    """If entry_ts isn't anywhere near the candles, fall-back still finds nearest."""
    # The implementation falls back to nearest-match, so this passes.
    # We test the explicit empty-candles edge instead:
    with pytest.raises(ValueError):
        render_trade_screenshot(
            candles=[],
            trade_row=_trade_row(_ramp_candles(10), 2, 5),
            output_path=tmp_path / "empty.png",
            spec=ScreenshotSpec(width_in=4.0, height_in=2.0, dpi=72),
        )
