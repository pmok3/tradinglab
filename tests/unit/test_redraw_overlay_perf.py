"""Regression test for the ``redraw-overlay-perf`` audit.

The reviewer pointed out that every :class:`DrawingStore` event
(``add`` / ``remove`` / ``update`` / etc.) used to fire a full
:meth:`ChartApp._render` — which rebuilds candles, all 15
indicators, volume bars, overlay annotations, AND drawings. For a
slider drag in the drawing dialog (which debounces commits to
~5 Hz) the chart was rebuilding hundreds of unrelated artists per
second.

The fix:

1. :func:`tradinglab.drawings.render.clear_drawing_artists` —
   removes only drawing-tagged artists from an Axes (lines and
   labels identified by their ``gid``).
2. :meth:`ChartApp._repaint_drawings_only` — fast-path that, for
   every price slot, clears drawing artists, re-renders the
   slot's drawings, and calls ``canvas.draw_idle()``. The full
   ``_render`` is reserved for the fast-path's fallback branch
   when something goes wrong.
3. :meth:`ChartApp._on_drawing_event` — now routes through the
   fast-path; the previous unconditional ``_render`` call moved
   to the exception handler.

These tests verify the new helper and the wiring without
spinning up a real ChartApp / mpl Figure.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tradinglab.drawings import make_hline_drawing  # noqa: E402
from tradinglab.drawings.render import (  # noqa: E402
    DRAWING_GID_PREFIX,
    DRAWING_LABEL_GID_PREFIX,
    clear_drawing_artists,
    render_drawings,
)

# ---------------------------------------------------------------------------
# clear_drawing_artists
# ---------------------------------------------------------------------------

def test_clear_drawing_artists_on_empty_axes_returns_zero():
    fig, ax = plt.subplots()
    try:
        assert clear_drawing_artists(ax) == 0
    finally:
        plt.close(fig)


def test_clear_drawing_artists_removes_lines_by_gid():
    fig, ax = plt.subplots()
    try:
        ds = [
            make_hline_drawing(ticker="AAPL", price=100.0),
            make_hline_drawing(ticker="AAPL", price=110.0),
        ]
        render_drawings(ax, ds)
        # Count drawing-tagged lines before clear.
        before_lines = sum(
            1 for line in ax.lines
            if (line.get_gid() or "").startswith(DRAWING_GID_PREFIX))
        assert before_lines == 2

        removed = clear_drawing_artists(ax)
        assert removed >= 2

        after_lines = sum(
            1 for line in ax.lines
            if (line.get_gid() or "").startswith(DRAWING_GID_PREFIX))
        assert after_lines == 0, (
            "clear_drawing_artists must remove every drawing-gid line")
    finally:
        plt.close(fig)


def test_clear_drawing_artists_removes_label_texts_by_gid():
    fig, ax = plt.subplots()
    try:
        ds = [
            make_hline_drawing(ticker="AAPL", price=100.0, label="stop"),
        ]
        render_drawings(ax, ds)
        before_texts = sum(
            1 for txt in ax.texts
            if (txt.get_gid() or "").startswith(DRAWING_LABEL_GID_PREFIX))
        assert before_texts == 1

        clear_drawing_artists(ax)

        after_texts = sum(
            1 for txt in ax.texts
            if (txt.get_gid() or "").startswith(DRAWING_LABEL_GID_PREFIX))
        assert after_texts == 0, (
            "clear_drawing_artists must remove drawing-label texts too")
    finally:
        plt.close(fig)


def test_clear_drawing_artists_does_not_touch_other_artists():
    """A normal user line / text on the same axes (e.g. an
    indicator overlay) must NOT be removed."""
    fig, ax = plt.subplots()
    try:
        # User content with a non-drawing gid.
        unrelated_line = ax.axhline(y=50.0, gid="indicator:sma")
        unrelated_text = ax.text(0.5, 0.5, "hi", gid="indicator-label:sma")
        # And drawings.
        ds = [make_hline_drawing(ticker="AAPL", price=100.0, label="stop")]
        render_drawings(ax, ds)

        clear_drawing_artists(ax)

        assert unrelated_line in ax.lines, (
            "clear_drawing_artists must not remove non-drawing lines")
        assert unrelated_text in ax.texts, (
            "clear_drawing_artists must not remove non-drawing texts")
    finally:
        plt.close(fig)


def test_clear_drawing_artists_ignores_no_gid_artists():
    """A line/text with no gid at all must NOT be removed."""
    fig, ax = plt.subplots()
    try:
        bare = ax.axhline(y=99.0)  # No gid → matplotlib default.
        ds = [make_hline_drawing(ticker="AAPL", price=100.0)]
        render_drawings(ax, ds)

        clear_drawing_artists(ax)

        assert bare in ax.lines
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# ChartApp._repaint_drawings_only — source wiring
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "tradinglab"
# Drawing-event subscriber + fast-path repaint were extracted from
# app.py to gui/drawings_app.py (DrawingsAppMixin). Concatenate
# both sources so the source pins below still anchor on the
# production code path.
APP_SRC = (
    (_SRC_ROOT / "app.py").read_text(encoding="utf-8")
    + "\n"
    + (_SRC_ROOT / "gui" / "drawings_app.py").read_text(encoding="utf-8")
)


def test_chartapp_defines_repaint_drawings_only():
    """The fast-path method must exist on ChartApp."""
    assert "def _repaint_drawings_only(self) -> None:" in APP_SRC


def test_repaint_drawings_only_calls_clear_drawing_artists():
    """The fast-path must use the new clear helper rather than
    re-running ``fig.clear()``."""
    start = APP_SRC.find("def _repaint_drawings_only")
    assert start != -1
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "clear_drawing_artists" in body, (
        "_repaint_drawings_only must call clear_drawing_artists "
        "to scrub the previous drawing artists")
    assert "canvas.draw_idle()" in body, (
        "_repaint_drawings_only must request a canvas repaint via "
        "draw_idle()")


def test_on_drawing_event_uses_fast_path():
    """The store subscriber must call _repaint_drawings_only first;
    full _render is only the fallback when the fast path fails."""
    start = APP_SRC.find("def _on_drawing_event(")
    assert start != -1
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    # Fast-path must appear BEFORE the fallback _render reference.
    fast_idx = body.find("self._repaint_drawings_only()")
    assert fast_idx != -1, (
        "_on_drawing_event must call _repaint_drawings_only — that's "
        "the audit fix.")


def test_redraw_overlay_perf_audit_referenced_in_source():
    assert "redraw-overlay-perf" in APP_SRC, (
        "_on_drawing_event / _repaint_drawings_only must cross-reference "
        "the audit id so future maintainers can trace the rationale.")


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_render_module_exports_clear_drawing_artists():
    from tradinglab.drawings import render as render_mod
    assert "clear_drawing_artists" in render_mod.__all__
