"""Tests for the per-symbol parallel screenshot pool.

``_render_screenshots_for_symbol`` used to render PNGs sequentially inside
each worker — 60 trades at ~80ms/PNG ≈ 5s of serial matplotlib work. It now
runs each trade through a small ``ThreadPoolExecutor`` (cap 4) because
``render_trade_screenshot`` constructs a fresh ``Figure()`` + ``FigureCanvasAgg``
per call (no global ``pyplot``) and is therefore safe to parallelise.

These tests pin:

1. Every trade row is rendered to a non-zero PNG on disk.
2. Cancellation honoured — once the token trips, queued tasks short-circuit
   without writing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tradinglab.models import Candle
from tradinglab.strategy_tester import runner
from tradinglab.strategy_tester.acceptance import AcceptanceToken
from tradinglab.strategy_tester.screenshot import ScreenshotSpec


def _candles(n: int) -> list[Candle]:
    t = datetime(2024, 6, 3, 9, 30)
    out: list[Candle] = []
    p = 100.0
    for _ in range(n):
        out.append(Candle(
            date=t, open=p, high=p + 0.5, low=p - 0.5, close=p + 0.2,
            volume=1000, session="regular",
        ))
        p += 0.1
        t += timedelta(minutes=5)
    return out


def _fake_trade_row(symbol: str, idx: int):
    """Build a minimal TradeRow-shaped namespace.

    ``_render_screenshots_for_symbol`` only touches ``row.pre`` (must exist
    even if ``None``), ``row.post.symbol``, ``row.post.ref_pre_trade_id``,
    and ``row.post.entry_ts``. We don't need a real ``PostTradeReview``.
    """
    post = MagicMock()
    post.symbol = symbol
    post.ref_pre_trade_id = None
    post.entry_ts = 1_700_000_000 + idx
    row = MagicMock()
    row.pre = None
    row.post = post
    return row


def test_all_trades_rendered_to_disk(tmp_path, monkeypatch):
    n_trades = 12
    rows = [_fake_trade_row("AAPL", i) for i in range(n_trades)]

    # Patch build_trade_rows so the renderer sees our synthetic rows.
    monkeypatch.setattr(runner, "build_trade_rows", lambda _result: rows)

    written_paths: list[Path] = []

    def _fake_render(*, candles, trade_row, output_path, spec,
                    entry_strategy=None, exit_strategy=None):
        # Match real render_trade_screenshot: write some bytes atomically.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 256)
        written_paths.append(output_path)

    monkeypatch.setattr(runner, "render_trade_screenshot", _fake_render)

    result = MagicMock()
    result.spec.symbol = "AAPL"

    count = runner._render_screenshots_for_symbol(
        candles=_candles(50),
        result=result,
        run_dir=tmp_path,
        screenshot_spec=ScreenshotSpec(),
    )

    assert count == n_trades
    pngs = sorted((tmp_path / "screenshots").glob("*.png"))
    assert len(pngs) == n_trades
    for p in pngs:
        assert p.stat().st_size > 0


def test_cancel_token_short_circuits(tmp_path, monkeypatch):
    rows = [_fake_trade_row("AAPL", i) for i in range(20)]
    monkeypatch.setattr(runner, "build_trade_rows", lambda _result: rows)

    token = AcceptanceToken()
    token.cancel()

    rendered: list[Path] = []

    def _fake_render(*, output_path, **_kw):  # noqa: ARG001 — kw discarded
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        rendered.append(output_path)

    monkeypatch.setattr(runner, "render_trade_screenshot", _fake_render)

    result = MagicMock()
    result.spec.symbol = "AAPL"

    count = runner._render_screenshots_for_symbol(
        candles=_candles(20),
        result=result,
        run_dir=tmp_path,
        screenshot_spec=ScreenshotSpec(),
        cancel_token=token,
    )

    # All tasks short-circuit on the cancelled token; none of them render.
    assert count == 0
    assert rendered == []


def test_no_spec_is_noop(tmp_path):
    result = MagicMock()
    result.spec.symbol = "AAPL"
    assert runner._render_screenshots_for_symbol(
        candles=_candles(5),
        result=result,
        run_dir=tmp_path,
        screenshot_spec=None,
    ) == 0


def test_no_trades_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "build_trade_rows", lambda _result: [])
    result = MagicMock()
    result.spec.symbol = "AAPL"
    assert runner._render_screenshots_for_symbol(
        candles=_candles(5),
        result=result,
        run_dir=tmp_path,
        screenshot_spec=ScreenshotSpec(),
    ) == 0


@pytest.mark.skipif(
    "CI" in __import__("os").environ,
    reason="Timing-based assertion is flaky in CI",
)
def test_parallel_is_faster_than_sequential(tmp_path, monkeypatch):
    """Smoke check that the pool actually parallelises.

    We mock render_trade_screenshot to sleep 50ms per call. Sequential would
    take ~12 * 50ms = 600ms; with 4 workers it should be ~150-250ms.
    """
    import time

    n_trades = 12
    rows = [_fake_trade_row("AAPL", i) for i in range(n_trades)]
    monkeypatch.setattr(runner, "build_trade_rows", lambda _result: rows)

    def _slow_render(*, output_path, **_kw):  # noqa: ARG001
        time.sleep(0.05)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")

    monkeypatch.setattr(runner, "render_trade_screenshot", _slow_render)

    result = MagicMock()
    result.spec.symbol = "AAPL"

    start = time.perf_counter()
    runner._render_screenshots_for_symbol(
        candles=_candles(5),
        result=result,
        run_dir=tmp_path,
        screenshot_spec=ScreenshotSpec(),
    )
    elapsed = time.perf_counter() - start

    sequential_estimate = n_trades * 0.05
    assert elapsed < sequential_estimate * 0.7, (
        f"parallel pool should be measurably faster than sequential "
        f"({elapsed:.3f}s vs sequential ~{sequential_estimate:.3f}s)"
    )
