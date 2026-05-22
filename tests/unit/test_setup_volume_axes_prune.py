"""Audit ``volume-axis-prune-both``.

Verifies that ``rendering.setup_volume_axes`` configures the y-axis
major locator to prune BOTH the bottom-most tick (always ``0`` —
volume ylims are pinned to ``(0.0, vmax * 1.1)``) and the top-most
tick (collides with the bottom-most price tick on the price pane
directly above via ``hspace=0``).

Regression guard: an earlier revision used ``prune="upper"`` which
left a "0" label sitting at the boundary between the volume pane
and whichever indicator pane the user had placed below it — the
"0" looked like it belonged to the next pane and made the visual
boundary fuzzy. The fix forces ``prune="both"`` so the bottom 0
tick is removed; volume = 0 is already visually obvious from the
bar reaching the pane's bottom edge.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from tradinglab.rendering import setup_volume_axes  # noqa: E402


def test_volume_axes_locator_prunes_both():
    """The locator must prune both upper and lower endpoints."""
    fig, ax = plt.subplots()
    try:
        setup_volume_axes(ax)
        loc = ax.yaxis.get_major_locator()
        assert isinstance(loc, MaxNLocator)
        # Public MaxNLocator state stores the user kwargs on ``_prune``.
        assert getattr(loc, "_prune", None) == "both"
    finally:
        plt.close(fig)


def test_volume_axes_no_zero_tick_label_for_typical_range():
    """For a typical (0, vmax) range, ``0`` must not appear as a tick label.

    Sets a representative volume range, draws the figure to force tick
    computation, and asserts no ``0`` (or empty/integer-format ``0``)
    tick label is emitted.
    """
    fig, ax = plt.subplots()
    try:
        setup_volume_axes(ax)
        ax.set_ylim(0.0, 3_300_000.0)
        fig.canvas.draw()
        labels = [t.get_text() for t in ax.get_yticklabels()]
        # ``fmt_volume`` would render zero as "0"; assert it is absent.
        assert "0" not in labels, (
            f"unexpected '0' tick label in volume axis: {labels!r}"
        )
    finally:
        plt.close(fig)


def test_volume_axes_no_zero_tick_label_for_small_range():
    """A small share-count range (no K/M suffix) must also drop the 0 tick."""
    fig, ax = plt.subplots()
    try:
        setup_volume_axes(ax)
        ax.set_ylim(0.0, 500.0)
        fig.canvas.draw()
        labels = [t.get_text() for t in ax.get_yticklabels()]
        assert "0" not in labels, (
            f"unexpected '0' tick label in small-range volume axis: {labels!r}"
        )
    finally:
        plt.close(fig)


def test_volume_axes_retains_at_least_one_visible_label():
    """Pruning both ends with nbins=3 must still leave at least one tick.

    Trader perspective: a totally empty volume y-axis would lose the
    scale entirely (no way to read absolute volume). MaxNLocator with
    nbins=3 + prune=both yields up to 2 interior ticks; the assertion
    here guards against a future regression that bumps nbins down to 2
    (which would prune to zero ticks).
    """
    fig, ax = plt.subplots()
    try:
        setup_volume_axes(ax)
        ax.set_ylim(0.0, 5_000_000.0)
        fig.canvas.draw()
        labels = [t.get_text() for t in ax.get_yticklabels() if t.get_text()]
        assert len(labels) >= 1, (
            "volume axis pruned to zero labels — at least one is required "
            "to give the user a magnitude readout"
        )
    finally:
        plt.close(fig)
