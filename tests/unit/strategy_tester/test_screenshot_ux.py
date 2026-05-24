"""Regression tests for the three trade-screenshot UX fixes.

Covers:

1. Time x-axis labels visible on the PRICE pane (not just the volume
   pane). ``sharex`` previously hid them, so a user whose volume pane
   was empty / "Volume unavailable" saw no time labels at all.
2. Entry/exit price labels carry a white bbox + arrow leader so they
   don't overlap the candles.
3. Title shows ``entry_strategy.name`` instead of ``"(no setup)"``
   when ``trade_row.setup_tag`` is empty. When neither is available
   the setup segment is omitted entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from tradinglab.backtest.journal import PostTradeReview, PreTradeEntry
from tradinglab.backtest.performance import TradeRow
from tradinglab.models import Candle
from tradinglab.strategy_tester.screenshot import (
    ScreenshotSpec,
    _draw_title_and_labels,
    render_trade_screenshot,
)


def _ramp_candles(n: int = 60) -> list[Candle]:
    start_ts = datetime(2026, 5, 12, 13, 35, tzinfo=timezone.utc)  # 09:35 ET
    bars: list[Candle] = []
    for i in range(n):
        op = 100.0 + i * 0.5
        bars.append(
            Candle(
                date=start_ts + timedelta(minutes=5 * i),
                open=op, high=op + 0.4, low=op - 0.4, close=op + 0.2,
                volume=1000 + i * 10,
                session="regular",
            )
        )
    return bars


def _trade_row(candles: list[Candle], entry_idx: int, exit_idx: int,
               *, setup_tag: str = "") -> TradeRow:
    ec, xc = candles[entry_idx], candles[exit_idx]
    entry_ts = int(ec.date.timestamp() * 1000.0)
    exit_ts = int(xc.date.timestamp() * 1000.0)
    qty = 100.0
    pnl = (xc.close - ec.open) * qty
    pre = PreTradeEntry(
        order_id="ord-ux",
        ts=entry_ts,
        symbol="AMD",
        side="buy",
        setup_tag=setup_tag,
        thesis="",
        conviction=3,
        size=qty,
        target=None,
    )
    post = PostTradeReview(
        symbol="AMD",
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=ec.open,
        exit_price=xc.close,
        quantity=qty,
        side="buy",
        pnl=pnl,
        pnl_pct=pnl / (ec.open * qty),
        mae=50.0,
        mfe=150.0,
        mae_pct=-0.005,
        mfe_pct=0.015,
        ref_pre_trade_id="ord-ux",
    )
    return TradeRow(post=post, pre=pre)


# A duck-typed stand-in for EntryStrategy that only carries the two
# fields ``_draw_title_and_labels`` reads. Avoids depending on the
# full EntryStrategy dataclass shape (which has many required fields).
@dataclass
class _FakeStrategy:
    name: str = ""
    id: str = "strat-xyz"


# ---------------------------------------------------------------------------
# Bug 1: time labels on the PRICE pane
# ---------------------------------------------------------------------------


def test_price_pane_xaxis_labelbottom_enabled(tmp_path) -> None:
    """`sharex` auto-hides the upper pane's tick labels — the
    screenshot pipeline must override that so the price pane shows
    its own time labels (Bug 1)."""
    candles = _ramp_candles(60)
    row = _trade_row(candles, entry_idx=15, exit_idx=35)
    out = tmp_path / "ux_bug1.png"
    render_trade_screenshot(
        candles=candles, trade_row=row, output_path=out,
        spec=ScreenshotSpec(width_in=6.0, height_in=3.5, dpi=72),
    )
    assert out.exists()


def test_price_pane_xaxis_tick_labels_present() -> None:
    """Directly poke at the price axes after layout: with sharex=True
    and our explicit ``tick_params(labelbottom=True)`` override, the
    price pane must produce non-empty x-tick labels.
    """
    candles = _ramp_candles(60)
    row = _trade_row(candles, entry_idx=15, exit_idx=35)
    # Patch print_png to capture the figure before it's discarded.
    captured: dict[str, object] = {}
    real_print_png = FigureCanvasAgg.print_png

    def _snoop(self, filename, *args, **kwargs):  # noqa: ANN001
        captured["fig"] = self.figure
        return real_print_png(self, filename, *args, **kwargs)

    FigureCanvasAgg.print_png = _snoop  # type: ignore[method-assign]
    try:
        out = candles[0].date.strftime("/tmp_unused.png")  # placeholder
        del out
        from pathlib import Path as _P
        tmp = _P(__file__).parent / "_ux_bug1_snoop.png"
        try:
            render_trade_screenshot(
                candles=candles, trade_row=row, output_path=tmp,
                spec=ScreenshotSpec(width_in=6.0, height_in=3.5, dpi=72),
            )
        finally:
            if tmp.exists():
                tmp.unlink()
    finally:
        FigureCanvasAgg.print_png = real_print_png  # type: ignore[method-assign]

    fig = captured["fig"]
    # The price pane is the FIRST axes (top of the gridspec).
    ax_price = fig.axes[0]
    # ``labelbottom`` must be True on the price pane after our override.
    params = ax_price.xaxis.get_tick_params()
    assert params.get("labelbottom") is True, (
        f"price pane labelbottom not enabled: {params!r}"
    )
    # And after a forced draw, the formatter must produce non-empty
    # labels for the tick positions inside the visible window.
    fig.canvas.draw()
    labels = [t.get_text() for t in ax_price.get_xticklabels()]
    non_empty = [s for s in labels if s.strip()]
    assert non_empty, (
        f"price pane has no non-empty tick labels: {labels!r}"
    )


# ---------------------------------------------------------------------------
# Bug 2: entry/exit labels with bbox + arrow
# ---------------------------------------------------------------------------


def test_entry_exit_annotations_have_bbox_and_arrow(tmp_path) -> None:
    """Both annotations must carry a white bbox patch AND an arrow
    leader so they're readable when placed away from their marker
    (Bug 2)."""
    candles = _ramp_candles(60)
    row = _trade_row(candles, entry_idx=15, exit_idx=35)

    captured: dict[str, object] = {}
    real_print_png = FigureCanvasAgg.print_png

    def _snoop(self, filename, *args, **kwargs):  # noqa: ANN001
        captured["fig"] = self.figure
        return real_print_png(self, filename, *args, **kwargs)

    FigureCanvasAgg.print_png = _snoop  # type: ignore[method-assign]
    try:
        out = tmp_path / "ux_bug2.png"
        render_trade_screenshot(
            candles=candles, trade_row=row, output_path=out,
            spec=ScreenshotSpec(width_in=6.0, height_in=3.5, dpi=72),
        )
    finally:
        FigureCanvasAgg.print_png = real_print_png  # type: ignore[method-assign]

    fig = captured["fig"]
    fig.canvas.draw()
    ax_price = fig.axes[0]
    annotations = [
        a for a in ax_price.texts
        if a.get_text().startswith(("Entry $", "Exit $"))
    ]
    assert len(annotations) == 2, (
        f"expected Entry + Exit annotations, got {[a.get_text() for a in annotations]!r}"
    )
    for a in annotations:
        # bbox: get_bbox_patch returns a FancyBboxPatch when bbox=dict(...)
        # was supplied at construction.
        assert a.get_bbox_patch() is not None, (
            f"annotation {a.get_text()!r} missing bbox patch"
        )
        # arrow: the annotation must have a non-None arrow_patch after
        # the figure is drawn (it's created lazily).
        assert a.arrow_patch is not None, (
            f"annotation {a.get_text()!r} missing arrow leader"
        )


# ---------------------------------------------------------------------------
# Bug 3: strategy name in title when setup_tag is empty
# ---------------------------------------------------------------------------


def test_title_uses_strategy_name_when_setup_tag_empty() -> None:
    candles = _ramp_candles(40)
    row = _trade_row(candles, entry_idx=10, exit_idx=20, setup_tag="")
    fig = Figure(figsize=(6.0, 3.5), dpi=72)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    _draw_title_and_labels(
        fig, ax, row, candles=candles, entry_index=10,
        entry_strategy=_FakeStrategy(name="EMA 3/8 cross"),
    )
    title = ax.get_title(loc="left")
    assert "EMA 3/8 cross" in title, f"strategy name missing: {title!r}"
    assert "(no setup)" not in title, (
        f"placeholder '(no setup)' should never appear: {title!r}"
    )


def test_title_prefers_setup_tag_over_strategy_name() -> None:
    """When both are present, setup_tag wins (user-authored, more
    specific) but the strategy name is appended after a separator."""
    candles = _ramp_candles(40)
    row = _trade_row(candles, entry_idx=10, exit_idx=20, setup_tag="my_setup")
    fig = Figure(figsize=(6.0, 3.5), dpi=72)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    _draw_title_and_labels(
        fig, ax, row, candles=candles, entry_index=10,
        entry_strategy=_FakeStrategy(name="EMA 3/8 cross"),
    )
    title = ax.get_title(loc="left")
    assert "setup: my_setup" in title, f"setup_tag missing: {title!r}"
    assert "EMA 3/8 cross" in title, f"strategy name missing: {title!r}"


def test_title_omits_setup_segment_when_both_missing() -> None:
    """No setup_tag AND no entry_strategy → no 'setup:' or '(no setup)'."""
    candles = _ramp_candles(40)
    row = _trade_row(candles, entry_idx=10, exit_idx=20, setup_tag="")
    fig = Figure(figsize=(6.0, 3.5), dpi=72)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    _draw_title_and_labels(fig, ax, row, candles=candles, entry_index=10)
    title = ax.get_title(loc="left")
    assert "setup:" not in title, (
        f"setup segment should be omitted when nothing to show: {title!r}"
    )
    assert "(no setup)" not in title, (
        f"placeholder '(no setup)' should never appear: {title!r}"
    )


def test_title_strategy_id_fallback_when_name_empty() -> None:
    """When the strategy carries an empty name, fall back to its id."""
    candles = _ramp_candles(40)
    row = _trade_row(candles, entry_idx=10, exit_idx=20, setup_tag="")
    fig = Figure(figsize=(6.0, 3.5), dpi=72)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    _draw_title_and_labels(
        fig, ax, row, candles=candles, entry_index=10,
        entry_strategy=_FakeStrategy(name="", id="strat-abc"),
    )
    assert "strat-abc" in ax.get_title(loc="left")
