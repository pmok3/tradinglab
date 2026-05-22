"""Unit tests for :mod:`tradinglab.drawings.render`."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # noqa: E402

from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from tradinglab.drawings import (  # noqa: E402
    DRAWING_ZORDER,
    Drawing,
    make_hline_drawing,
    render_drawings,
)
from tradinglab.drawings.render import (  # noqa: E402
    _LINESTYLE_MAP,
    DRAWING_GID_PREFIX,
    drawing_id_from_gid,
)


def _ax():
    fig = Figure()
    return fig.add_subplot(111)


# ---------------------------------------------------------------
# render_drawings
# ---------------------------------------------------------------

class TestRenderDrawings:
    def test_returns_one_artist_per_hline(self):
        ax = _ax()
        d1 = make_hline_drawing("AMD", 100.0)
        d2 = make_hline_drawing("AMD", 200.0, style="dashed", width=2.0)
        artists = render_drawings(ax, [d1, d2])
        assert len(artists) == 2
        for a in artists:
            assert isinstance(a, Line2D)

    def test_empty_input_returns_empty_list(self):
        ax = _ax()
        assert render_drawings(ax, []) == []

    def test_skips_non_hline_kinds(self):
        ax = _ax()
        # Build a Drawing directly with an unsupported kind.
        d = Drawing(kind="rect", id="x", ticker="AMD",
                    price=1.0, color="#000", width=1.0, style="solid")
        assert render_drawings(ax, [d]) == []

    def test_gid_set_to_drawing_id(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0)
        artists = render_drawings(ax, [d])
        gid = artists[0].get_gid()
        assert gid == f"{DRAWING_GID_PREFIX}{d.id}"

    def test_zorder_above_candles(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0)
        artists = render_drawings(ax, [d])
        assert artists[0].get_zorder() == DRAWING_ZORDER

    def test_linewidth_propagated(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, width=3.5)
        artists = render_drawings(ax, [d])
        assert artists[0].get_linewidth() == 3.5

    def test_color_propagated(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, color="#FF0000")
        artists = render_drawings(ax, [d])
        # Matplotlib normalizes the color to lowercase RGBA tuple
        # or a string; either way the original survives via to_rgba.
        from matplotlib.colors import to_rgba
        assert to_rgba(artists[0].get_color()) == to_rgba("#FF0000")

    def test_linestyle_solid(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, style="solid")
        a = render_drawings(ax, [d])[0]
        assert a.get_linestyle() == _LINESTYLE_MAP["solid"]

    def test_linestyle_dashed(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, style="dashed")
        a = render_drawings(ax, [d])[0]
        assert a.get_linestyle() == _LINESTYLE_MAP["dashed"]

    def test_linestyle_dotted(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, style="dotted")
        a = render_drawings(ax, [d])[0]
        assert a.get_linestyle() == _LINESTYLE_MAP["dotted"]

    def test_linestyle_dashdot(self):
        # Audit ``drawing-style-options``: ``dashdot`` was added
        # as a fourth, markedly-distinct style. The renderer's
        # linestyle map must carry the matplotlib alias ``"-."``
        # so the canvas reflects the user's pick.
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, style="dashdot")
        a = render_drawings(ax, [d])[0]
        assert a.get_linestyle() == _LINESTYLE_MAP["dashdot"]
        assert _LINESTYLE_MAP["dashdot"] == "-."

    def test_picker_enabled(self):
        # Kept on for future drag support; primary hit-test path
        # is `pick_drawing`, not pick events.
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0)
        a = render_drawings(ax, [d])[0]
        assert a.get_picker() is not None
        assert a.get_picker() is not False

    def test_per_drawing_error_isolated(self):
        ax = _ax()
        good = make_hline_drawing("AMD", 100.0)
        # Inject a Drawing whose price is NaN — axhline may raise
        # downstream or just plot off-screen, depending on matplotlib
        # version. Either way, the good drawing should still render.
        from math import nan
        bad = Drawing(kind="hline", id="bad", ticker="AMD",
                      price=nan, color="#000", width=1.0, style="solid")
        artists = render_drawings(ax, [bad, good])
        # We require at least the good drawing rendered.
        gids = [a.get_gid() for a in artists]
        assert f"{DRAWING_GID_PREFIX}{good.id}" in gids


# ---------------------------------------------------------------
# drawing_id_from_gid
# ---------------------------------------------------------------

class TestDrawingIdFromGid:
    def test_extracts_uuid(self):
        assert drawing_id_from_gid("drawing:abc123") == "abc123"

    def test_extracts_full_hex(self):
        d = make_hline_drawing("AMD", 1.0)
        gid = f"drawing:{d.id}"
        assert drawing_id_from_gid(gid) == d.id

    def test_none_returns_none(self):
        assert drawing_id_from_gid(None) is None

    def test_empty_returns_none(self):
        assert drawing_id_from_gid("") is None

    def test_no_prefix_returns_none(self):
        assert drawing_id_from_gid("other:abc") is None
        assert drawing_id_from_gid("abc") is None

    def test_prefix_only_returns_none(self):
        assert drawing_id_from_gid("drawing:") is None

    def test_non_string_returns_none(self):
        assert drawing_id_from_gid(123) is None  # type: ignore[arg-type]

    def test_label_gid_prefix_also_unwraps(self):
        """Label artists carry ``drawing-label:<id>`` gids; the
        helper unwraps both line and label gids so pick-event
        callers can find the originating drawing from either."""
        from tradinglab.drawings.render import DRAWING_LABEL_GID_PREFIX
        assert drawing_id_from_gid(
            f"{DRAWING_LABEL_GID_PREFIX}abc") == "abc"
        assert drawing_id_from_gid(
            f"{DRAWING_LABEL_GID_PREFIX}") is None


# ---------------------------------------------------------------
# Label rendering (regression #C3, 2026-05 adversarial review)
# ---------------------------------------------------------------

class TestLabelRendering:
    """An earlier version of :func:`render_drawings` ignored the
    ``label`` field. A user could type ``"stop"`` / ``"TP1"`` /
    ``"max pain"`` into the dialog, watch the value persist to
    ``drawings.json``, and never see it appear on the chart. This
    test class locks in the now-real label rendering pipeline.
    """

    def _find_label_text(self, ax, drawing_id: str):
        from tradinglab.drawings.render import DRAWING_LABEL_GID_PREFIX
        target = f"{DRAWING_LABEL_GID_PREFIX}{drawing_id}"
        for child in ax.get_children():
            try:
                if child.get_gid() == target:
                    return child
            except Exception:  # noqa: BLE001
                continue
        return None

    def test_empty_label_skips_text_artist(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0)  # label="" default
        render_drawings(ax, [d])
        assert self._find_label_text(ax, d.id) is None

    def test_whitespace_only_label_skips_text_artist(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, label="   ")
        render_drawings(ax, [d])
        assert self._find_label_text(ax, d.id) is None

    def test_label_renders_text_artist(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, label="stop")
        render_drawings(ax, [d])
        txt = self._find_label_text(ax, d.id)
        assert txt is not None, (
            "render_drawings must paint a Text artist for any "
            "non-empty label (regression C3)")
        assert txt.get_text() == "stop"

    def test_label_color_matches_line_color(self):
        from matplotlib.colors import to_rgba
        ax = _ax()
        d = make_hline_drawing(
            "AMD", 100.0, color="#FF00AA", label="TP1")
        render_drawings(ax, [d])
        txt = self._find_label_text(ax, d.id)
        assert txt is not None
        assert to_rgba(txt.get_color()) == to_rgba("#FF00AA")

    def test_label_aligned_to_right_edge(self):
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, label="resistance")
        render_drawings(ax, [d])
        txt = self._find_label_text(ax, d.id)
        assert txt is not None
        x, _y = txt.get_position()
        # Anchored ~rightmost axes fraction; we accept anything
        # firmly on the right half so future tweaks to the exact
        # pad value don't break the test.
        assert 0.9 <= float(x) <= 1.0
        assert txt.get_horizontalalignment() == "right"

    def test_label_zorder_above_line(self):
        from tradinglab.drawings.render import (
            DRAWING_LABEL_ZORDER,
            DRAWING_ZORDER,
        )
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, label="hi")
        render_drawings(ax, [d])
        txt = self._find_label_text(ax, d.id)
        assert txt is not None
        assert txt.get_zorder() >= DRAWING_ZORDER
        assert txt.get_zorder() == DRAWING_LABEL_ZORDER

    def test_label_does_not_break_line_creation(self):
        """A label-render error must not blank the underlying
        line — they're independent code paths."""
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, label="ok")
        artists = render_drawings(ax, [d])
        assert len(artists) == 1
        assert artists[0].get_gid() == f"{DRAWING_GID_PREFIX}{d.id}"

    def test_multiple_drawings_each_get_their_own_label(self):
        ax = _ax()
        d1 = make_hline_drawing("AMD", 100.0, label="stop")
        d2 = make_hline_drawing("AMD", 200.0, label="target")
        render_drawings(ax, [d1, d2])
        t1 = self._find_label_text(ax, d1.id)
        t2 = self._find_label_text(ax, d2.id)
        assert t1 is not None and t1.get_text() == "stop"
        assert t2 is not None and t2.get_text() == "target"

    def test_label_text_artist_not_in_returned_list(self):
        """``render_drawings`` returns Line2D artists only; the
        Text artist is owned by the axes (cleared on next render
        via ``fig.clear()``) and not returned."""
        ax = _ax()
        d = make_hline_drawing("AMD", 100.0, label="x")
        artists = render_drawings(ax, [d])
        for a in artists:
            assert isinstance(a, Line2D)
