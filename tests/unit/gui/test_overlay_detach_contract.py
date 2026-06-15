"""Overlay artist-detach contract — Stage 0 of the topology-preserving paint
pipeline (``docs/PAINT_PIPELINE_REFACTOR.md``).

Every chart overlay's ``clear()`` must DETACH its matplotlib artists from the
axes, not merely drop Python refs. The fast path will clear + redraw WITHOUT a
surrounding ``figure.clear()``, so a ref-only ``clear()`` would leave orphaned
artists on the reused axes and duplicate them on the next paint.

Each overlay's ``clear()`` must:
  * remove every Line2D / Text it owns from the axes,
  * leave its artist registry empty,
  * be idempotent (a second ``clear()`` must not raise),
  * tolerate already-detached artists (the legacy ``figure.clear()`` flow).
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from tradinglab.core import thread_guard  # noqa: E402
from tradinglab.gui.entries_overlay import EntriesOverlay  # noqa: E402
from tradinglab.gui.evidence_overlay import EvidenceOverlay  # noqa: E402
from tradinglab.gui.exits_overlay import ExitsOverlay  # noqa: E402
from tradinglab.gui.live_price_overlay import LivePriceOverlay  # noqa: E402


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


def _pair(ax):
    """A (Line2D, Text) pair freshly attached to ``ax``."""
    (line,) = ax.plot([0, 1], [0, 1])
    txt = ax.text(0.5, 0.5, "x")
    assert line in ax.lines and txt in ax.texts
    return line, txt


def test_live_price_clear_detaches_and_is_idempotent():
    fig = plt.figure()
    ax = fig.add_subplot()
    try:
        ov = LivePriceOverlay()
        line, txt = _pair(ax)
        ov._artists["primary"] = (line, txt)
        ov.clear()
        assert line not in ax.lines and txt not in ax.texts  # truly detached
        assert ov.slot_count == 0
        ov.clear()  # idempotent — must not raise
    finally:
        plt.close(fig)


def test_evidence_clear_detaches_and_is_idempotent():
    fig = plt.figure()
    ax = fig.add_subplot()
    try:
        ov = EvidenceOverlay()
        line, txt = _pair(ax)
        ov._artists.append((line, txt))
        ov.clear()
        assert line not in ax.lines and txt not in ax.texts
        assert ov.marker_count == 0
        ov.clear()
    finally:
        plt.close(fig)


def test_entries_clear_detaches_and_is_idempotent():
    fig = plt.figure()
    ax = fig.add_subplot()
    try:
        ov = EntriesOverlay(evaluator=object())
        line, txt = _pair(ax)
        ov._artists["k"] = [(line, txt)]
        ov.clear()
        assert line not in ax.lines and txt not in ax.texts
        assert ov.line_count == 0
        ov.clear()
    finally:
        plt.close(fig)


def test_exits_clear_detaches_and_is_idempotent():
    fig = plt.figure()
    ax = fig.add_subplot()
    try:
        tracker = SimpleNamespace(subscribe=lambda cb: None)
        ov = ExitsOverlay(evaluator=object(), tracker=tracker)
        line, txt = _pair(ax)
        ov._artists["pos1"] = [(line, txt)]
        ov.clear()
        assert line not in ax.lines and txt not in ax.texts
        assert ov.line_count == 0
        ov.clear()
    finally:
        plt.close(fig)


def test_clear_tolerates_already_detached_artists():
    """Legacy flow: figure.clear() already detached the artists → clear()
    must swallow the resulting .remove() errors and still empty the registry."""
    fig = plt.figure()
    ax = fig.add_subplot()
    try:
        ov = LivePriceOverlay()
        line, txt = _pair(ax)
        ov._artists["primary"] = (line, txt)
        line.remove()
        txt.remove()  # pre-detached (figure.clear analogue)
        ov.clear()
        assert ov.slot_count == 0
    finally:
        plt.close(fig)


def test_interaction_detach_overlay_artists():
    """``InteractionMixin._detach_overlay_artists`` removes the crosshair /
    price-label / time-label / readout / hover artists from their axes, and is
    idempotent. Driven via the unbound method on a lightweight stub (it only
    reads the registry attributes)."""
    from matplotlib.offsetbox import AnchoredOffsetbox, TextArea

    from tradinglab.gui.interaction import InteractionMixin

    fig = plt.figure()
    ax = fig.add_subplot()
    try:
        vline = ax.axvline(0)
        hline = ax.axhline(0)
        plabel = ax.annotate("", xy=(0, 0))
        tlabel = ax.annotate("", xy=(0, 0))
        hover = ax.annotate("", xy=(0, 0))
        box = AnchoredOffsetbox(loc="upper left", child=TextArea("x"))
        ax.add_artist(box)
        arts = [vline, hline, plabel, tlabel, hover, box]
        assert all(a.axes is ax for a in arts)

        stub = SimpleNamespace(
            _crosshair_artists={ax: (vline, hline)},
            _price_label_artists={ax: plabel},
            _time_label_artists={"primary": tlabel},
            _readout_artists={ax: box},
            _hover_ann=hover,
        )
        InteractionMixin._detach_overlay_artists(stub)
        assert all(a.axes is None for a in arts)  # every overlay detached
        InteractionMixin._detach_overlay_artists(stub)  # idempotent — no raise
    finally:
        plt.close(fig)


def test_interaction_detach_tolerates_missing_registries():
    from tradinglab.gui.interaction import InteractionMixin

    InteractionMixin._detach_overlay_artists(SimpleNamespace())  # must not raise
