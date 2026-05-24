"""Tests for strategy_tester.runner's screenshot fan-out logic.

The mechanical evaluator does NOT create PreTradeEntry records, so every
TradeRow returned by build_trade_rows has ``row.pre = None``. Prior to
the fix, ``_render_screenshots_for_symbol`` only consulted
``row.pre.order_id`` to build the filename, which silently collapsed all
60 trades per symbol onto a single ``<SYM>_unknown_post.png`` (the user
saw exactly 3 PNGs for 180 trades). This module pins the fallback chain:
``pre.order_id → post.ref_pre_trade_id → f"t{entry_ts}"``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest  # noqa: F401  -- imported for future parametrize use

from tradinglab.backtest.journal import PostTradeReview
from tradinglab.backtest.session import SessionResult, SessionSpec
from tradinglab.models import Candle
from tradinglab.strategy_tester.runner import _render_screenshots_for_symbol
from tradinglab.strategy_tester.screenshot import ScreenshotSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ramp_candles(n: int = 80) -> list[Candle]:
    start_ts = datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc)
    bars: list[Candle] = []
    for i in range(n):
        op = 100.0 + i * 0.5
        bars.append(Candle(
            date=start_ts + timedelta(minutes=5 * i),
            open=op, high=op + 0.4, low=op - 0.4, close=op + 0.2,
            volume=1000 + i * 10, session="regular",
        ))
    return bars


def _post(
    candles: list[Candle],
    entry_idx: int,
    exit_idx: int,
    *,
    ref_pre_trade_id: str | None = None,
    symbol: str = "AAPL",
) -> PostTradeReview:
    ec = candles[entry_idx]
    xc = candles[exit_idx]
    entry_ts = int(ec.date.timestamp() * 1000.0)
    exit_ts = int(xc.date.timestamp() * 1000.0)
    qty = 100.0
    pnl = (xc.close - ec.open) * qty
    return PostTradeReview(
        symbol=symbol,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=ec.open,
        exit_price=xc.close,
        quantity=qty,
        side="buy",
        pnl=pnl,
        pnl_pct=pnl / (ec.open * qty) if ec.open and qty else 0.0,
        mae=10.0,
        mfe=50.0,
        mae_pct=-0.001,
        mfe_pct=0.005,
        ref_pre_trade_id=ref_pre_trade_id,
    )


def _make_result(symbol: str, posts: list[PostTradeReview]) -> SessionResult:
    """Build a minimal SessionResult with the supplied post-trades.

    The screenshot fan-out only consults ``result.post_trades`` (via
    ``build_trade_rows``); fills/pre_trades are intentionally empty so
    every TradeRow has ``pre=None`` — mirroring the mechanical
    evaluator's output exactly.
    """
    spec = SessionSpec(
        deck_seed=0,
        tickers=(symbol,),
        start_clock_iso="",
        slippage_bps=0.0,
        commission=0.0,
    )
    return SessionResult(
        spec=spec,
        fills=[],
        pre_trades=[],          # mechanical evaluator never emits these
        post_trades=posts,
        equity_curve=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_render_screenshots_uses_ref_pre_trade_id_when_pre_missing(tmp_path: Path) -> None:
    """When row.pre is None but post.ref_pre_trade_id is set, the filename
    must use the ref_pre_trade_id so each trade gets its own PNG.

    Pre-fix: all PNGs collided on <SYM>_unknown_post.png.
    """
    candles = _ramp_candles(80)
    posts = [
        _post(candles, entry_idx=10, exit_idx=15, ref_pre_trade_id="ord-A"),
        _post(candles, entry_idx=20, exit_idx=25, ref_pre_trade_id="ord-B"),
        _post(candles, entry_idx=30, exit_idx=35, ref_pre_trade_id="ord-C"),
    ]
    result = _make_result("AAPL", posts)

    written = _render_screenshots_for_symbol(
        candles=candles,
        result=result,
        run_dir=tmp_path,
        screenshot_spec=ScreenshotSpec(width_in=6.0, height_in=3.5, dpi=72),
    )
    assert written == 3, f"expected 3 PNGs, got {written}"

    shots_dir = tmp_path / "screenshots"
    pngs = sorted(p.name for p in shots_dir.glob("*.png"))
    assert pngs == [
        "AAPL_ord-A_post.png",
        "AAPL_ord-B_post.png",
        "AAPL_ord-C_post.png",
    ], f"unexpected filenames: {pngs}"


def test_render_screenshots_falls_back_to_entry_ts_when_no_ids(tmp_path: Path) -> None:
    """When BOTH row.pre is None AND post.ref_pre_trade_id is None
    (purely mechanical run, no journal linkage), the filename must
    fall back to a stable per-trade key derived from entry_ts so the
    PNGs don't collide on a shared <SYM>_unknown_post.png.
    """
    candles = _ramp_candles(80)
    # All three trades have ref_pre_trade_id=None — the worst case.
    posts = [
        _post(candles, entry_idx=10, exit_idx=15, ref_pre_trade_id=None),
        _post(candles, entry_idx=20, exit_idx=25, ref_pre_trade_id=None),
        _post(candles, entry_idx=30, exit_idx=35, ref_pre_trade_id=None),
    ]
    result = _make_result("AAPL", posts)

    written = _render_screenshots_for_symbol(
        candles=candles,
        result=result,
        run_dir=tmp_path,
        screenshot_spec=ScreenshotSpec(width_in=6.0, height_in=3.5, dpi=72),
    )
    assert written == 3

    shots_dir = tmp_path / "screenshots"
    pngs = sorted(p.name for p in shots_dir.glob("*.png"))
    assert len(pngs) == 3, f"expected 3 unique PNGs, got {pngs}"
    # All filenames start with the symbol and include a 't<ts>' marker.
    for name in pngs:
        assert name.startswith("AAPL_t"), name
        assert name.endswith("_post.png"), name
    assert "AAPL_unknown_post.png" not in pngs


def test_render_screenshots_off_when_spec_is_none(tmp_path: Path) -> None:
    """Sanity: screenshot_spec=None skips the whole pipeline."""
    candles = _ramp_candles(40)
    posts = [_post(candles, 10, 15, ref_pre_trade_id="ord-A")]
    result = _make_result("AAPL", posts)
    written = _render_screenshots_for_symbol(
        candles=candles,
        result=result,
        run_dir=tmp_path,
        screenshot_spec=None,
    )
    assert written == 0
    assert not (tmp_path / "screenshots").exists()


# ---------------------------------------------------------------------------
# Regression: every screenshot rendered the SAME first-window of candles
# because PostTradeReview.entry_ts is in epoch seconds (strategy_tester
# evaluator threads bar_ts in seconds through to Fill.fill_ts), but
# screenshot._index_of_ts compared against c.date.timestamp() * 1000.0
# (milliseconds). Exact-match never hit; the nearest-neighbour fallback
# always picked the earliest candle (smallest |c_ms - ts_seconds|).
# ---------------------------------------------------------------------------


def _post_seconds(
    candles: list[Candle],
    entry_idx: int,
    exit_idx: int,
    *,
    symbol: str = "AAPL",
) -> PostTradeReview:
    """Build a PostTradeReview whose entry_ts/exit_ts are in **SECONDS**
    (matching what the strategy_tester evaluator actually emits)."""
    ec = candles[entry_idx]
    xc = candles[exit_idx]
    entry_ts = int(ec.date.timestamp())   # seconds, NOT milliseconds
    exit_ts = int(xc.date.timestamp())
    qty = 100.0
    pnl = (xc.close - ec.open) * qty
    return PostTradeReview(
        symbol=symbol,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=ec.open,
        exit_price=xc.close,
        quantity=qty,
        side="buy",
        pnl=pnl,
        pnl_pct=pnl / (ec.open * qty) if ec.open and qty else 0.0,
        mae=10.0,
        mfe=50.0,
        mae_pct=-0.001,
        mfe_pct=0.005,
        ref_pre_trade_id=None,
    )


def test_index_of_ts_handles_epoch_seconds() -> None:
    """`_index_of_ts` must locate a candle whose timestamp matches in
    seconds — the strategy_tester emits PostTradeReview.entry_ts as
    epoch seconds, not milliseconds."""
    from tradinglab.strategy_tester.screenshot import _index_of_ts

    candles = _ramp_candles(80)
    for i in [0, 5, 13, 47, 79]:
        ts_seconds = int(candles[i].date.timestamp())
        got = _index_of_ts(candles, ts_seconds)
        assert got == i, (
            f"expected _index_of_ts to return {i} for second-precision ts, "
            f"got {got}"
        )


def test_index_of_ts_handles_epoch_milliseconds() -> None:
    """Back-compat: `_index_of_ts` must still locate ms-precision
    timestamps (used historically by some journal records)."""
    from tradinglab.strategy_tester.screenshot import _index_of_ts

    candles = _ramp_candles(80)
    for i in [0, 5, 13, 47, 79]:
        ts_ms = int(candles[i].date.timestamp() * 1000.0)
        got = _index_of_ts(candles, ts_ms)
        assert got == i, (
            f"expected _index_of_ts to return {i} for ms-precision ts, "
            f"got {got}"
        )


def test_render_screenshots_renders_distinct_windows_for_seconds_ts(
    tmp_path: Path,
) -> None:
    """Regression for the "every screenshot is the same" bug.

    Three trades at three different timestamps (in seconds) must
    produce three visually distinct PNGs. We assert distinctness by
    byte-comparing the PNG bodies — different windows → different
    candle layouts → different pixel bytes.
    """
    candles = _ramp_candles(120)
    posts = [
        _post_seconds(candles, entry_idx=10, exit_idx=15),
        _post_seconds(candles, entry_idx=60, exit_idx=68),
        _post_seconds(candles, entry_idx=100, exit_idx=110),
    ]
    result = _make_result("AAPL", posts)

    written = _render_screenshots_for_symbol(
        candles=candles,
        result=result,
        run_dir=tmp_path,
        screenshot_spec=ScreenshotSpec(width_in=6.0, height_in=3.5, dpi=72),
    )
    assert written == 3

    shots_dir = tmp_path / "screenshots"
    pngs = sorted(shots_dir.glob("*.png"))
    assert len(pngs) == 3, f"expected 3 PNGs, got {len(pngs)}"

    # If the bar-index bug is back, all three PNGs will render the same
    # first-window of candles → identical byte content. We compare
    # bytes directly; even small differences in marker placement /
    # title datetime / x-axis labels will perturb the hash.
    contents = [p.read_bytes() for p in pngs]
    assert contents[0] != contents[1], (
        "first two screenshots are byte-identical — bar-index lookup is "
        "rendering the same window for both trades (the 'every screenshot "
        "is the same' regression)"
    )
    assert contents[1] != contents[2], (
        "screenshots 2 and 3 are byte-identical — bar-index regression"
    )
    assert contents[0] != contents[2], (
        "screenshots 1 and 3 are byte-identical — bar-index regression"
    )
