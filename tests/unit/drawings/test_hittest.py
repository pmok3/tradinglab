"""Unit tests for :func:`tradinglab.drawings.render.pick_drawing`."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # noqa: E402

from matplotlib.figure import Figure  # noqa: E402

from tradinglab.drawings import (  # noqa: E402
    Drawing,
    make_hline_drawing,
    pick_drawing,
)


def _ax(*, ylim=(0.0, 100.0)):
    """A Figure-bound axes large enough that transData gives stable
    pixel coords (Figure size is the matplotlib default, ~640x480)."""
    fig = Figure(figsize=(8, 6), dpi=100)
    ax = fig.add_subplot(111)
    ax.set_xlim(0.0, 100.0)
    ax.set_ylim(*ylim)
    # Force a draw so the transforms are realized.
    fig.canvas.draw()
    return ax


def _y_pixel(ax, price: float) -> float:
    _, y = ax.transData.transform((0.0, price))
    return float(y)


# ---------------------------------------------------------------
# basic hit
# ---------------------------------------------------------------

class TestPickDrawing:
    def test_empty_list_returns_none(self):
        ax = _ax()
        assert pick_drawing([], ax, 100.0, 100.0) is None

    def test_exact_pixel_hit(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        assert pick_drawing([d], ax, 100.0, y_px) is d

    def test_within_tolerance(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        # 4 px away — inside the default 5 px threshold.
        assert pick_drawing([d], ax, 100.0, y_px + 4.0) is d
        assert pick_drawing([d], ax, 100.0, y_px - 4.0) is d

    def test_outside_tolerance(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        # 10 px away — outside threshold.
        assert pick_drawing([d], ax, 100.0, y_px + 10.0) is None

    def test_tol_px_kwarg(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        # Larger tol catches the 10 px away cursor.
        assert pick_drawing([d], ax, 100.0, y_px + 10.0, tol_px=15.0) is d
        # Tighter tol misses a 4 px cursor.
        assert pick_drawing([d], ax, 100.0, y_px + 4.0, tol_px=2.0) is None


# ---------------------------------------------------------------
# tiebreaking
# ---------------------------------------------------------------

class TestPickDrawingTiebreaking:
    def test_closer_wins(self):
        ax = _ax()
        d1 = make_hline_drawing("AMD", 50.0)
        d2 = make_hline_drawing("AMD", 51.0)
        # Cursor sits closer to 50.5 → which one wins depends on
        # which line is closer in pixel space.
        y_target = 50.2  # closer to 50 than 51
        y_px = _y_pixel(ax, y_target)
        result = pick_drawing([d1, d2], ax, 100.0, y_px)
        assert result is d1

        y_target = 50.8  # closer to 51
        y_px = _y_pixel(ax, y_target)
        result = pick_drawing([d1, d2], ax, 100.0, y_px)
        assert result is d2

    def test_most_recent_on_tie(self):
        # Two drawings at exactly the same price — the later
        # one in the input list (= more recently added) wins.
        ax = _ax()
        d1 = make_hline_drawing("AMD", 50.0)
        d2 = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        result = pick_drawing([d1, d2], ax, 100.0, y_px)
        assert result is d2


# ---------------------------------------------------------------
# robustness
# ---------------------------------------------------------------

class TestPickDrawingRobust:
    def test_skips_non_hline(self):
        ax = _ax()
        d_rect = Drawing(kind="rect", id="r", ticker="AMD",
                         price=50.0, color="#000", width=1.0, style="solid")
        d_hl = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        result = pick_drawing([d_rect, d_hl], ax, 100.0, y_px)
        assert result is d_hl

    def test_no_axes_transform_returns_none(self):
        # Pass a junk axes whose transData attribute raises.
        d = make_hline_drawing("AMD", 50.0)

        class _BadAx:
            @property
            def transData(self):
                raise RuntimeError("no transform")

        assert pick_drawing([d], _BadAx(), 0.0, 0.0) is None


# ---------------------------------------------------------------
# DPI-scaled tolerance (audit ``pick-tolerance-dpi``)
# ---------------------------------------------------------------

def _ax_at_dpi(dpi: float, *, ylim=(0.0, 100.0)):
    """Figure-bound axes at a specific DPI for HiDPI testing."""
    fig = Figure(figsize=(8, 6), dpi=dpi)
    ax = fig.add_subplot(111)
    ax.set_xlim(0.0, 100.0)
    ax.set_ylim(*ylim)
    fig.canvas.draw()
    return ax


class TestPickDrawingDpiScale:
    """The tolerance must scale with figure DPI so a 5-logical-px
    threshold stays usable on a 4K HiDPI display.
    """

    def test_constant_exposed(self):
        from tradinglab.drawings.render import PICK_TOLERANCE_REFERENCE_DPI

        assert PICK_TOLERANCE_REFERENCE_DPI == 96.0

    def test_low_dpi_does_not_shrink_tolerance(self):
        # On a 72-DPI display the scale factor would be < 1.0 but
        # we floor to 1.0 so legacy displays keep the caller's
        # requested tolerance.
        ax = _ax_at_dpi(72)
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        # 4 px away with default tol=5.0 → must still hit.
        assert pick_drawing([d], ax, 100.0, y_px + 4.0) is d

    def test_high_dpi_doubles_tolerance(self):
        # 192 DPI → scale = 192/96 = 2.0 → 5 px logical = 10 px display.
        # An 8-px-away cursor would MISS at 96 DPI (default tol 5)
        # but should HIT at 192 DPI.
        ax = _ax_at_dpi(192)
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        assert pick_drawing([d], ax, 100.0, y_px + 8.0) is d
        # 9 px away still inside (< 10).
        assert pick_drawing([d], ax, 100.0, y_px + 9.0) is d
        # 11 px away outside (> 10).
        assert pick_drawing([d], ax, 100.0, y_px + 11.0) is None

    def test_retina_240_dpi_scales(self):
        # macOS Retina 240 DPI → scale = 2.5 → 5 px logical = 12.5 px display.
        ax = _ax_at_dpi(240)
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        # 12 px away — inside the 12.5 px effective tolerance.
        assert pick_drawing([d], ax, 100.0, y_px + 12.0) is d
        # 13 px away — just outside.
        assert pick_drawing([d], ax, 100.0, y_px + 13.0) is None

    def test_explicit_tol_px_also_scales(self):
        ax = _ax_at_dpi(192)
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        # Caller passes tol_px=10 → effective = 20 at 192 DPI.
        assert pick_drawing([d], ax, 100.0, y_px + 18.0, tol_px=10.0) is d
        assert pick_drawing([d], ax, 100.0, y_px + 22.0, tol_px=10.0) is None

    def test_default_96_dpi_unchanged_behavior(self):
        # Sanity: at the reference DPI the math is a no-op.
        ax = _ax_at_dpi(96)
        d = make_hline_drawing("AMD", 50.0)
        y_px = _y_pixel(ax, 50.0)
        # 4 px hits, 6 px misses (default tol 5).
        assert pick_drawing([d], ax, 100.0, y_px + 4.0) is d
        assert pick_drawing([d], ax, 100.0, y_px + 6.0) is None

    def test_missing_dpi_attr_falls_back(self):
        # If ax.figure.dpi is None / missing, fall back to reference
        # DPI (i.e. no scaling).
        d = make_hline_drawing("AMD", 50.0)

        class _StubAx:
            class transData:
                @staticmethod
                def transform(xy):
                    return (0.0, 100.0)
            transData = transData()  # noqa: F811
            figure = object()  # no .dpi attribute

        # Cursor at y_disp=104 from line at y_disp=100 → 4 px away,
        # within default tol of 5 → hit.
        assert pick_drawing([d], _StubAx(), 0.0, 104.0) is d
        assert pick_drawing([d], _StubAx(), 0.0, 106.0) is None
