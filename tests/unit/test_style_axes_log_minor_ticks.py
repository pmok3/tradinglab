"""Regression: ``rendering.style_axes`` themes MINOR tick labels too.

Bug: a log-scale indicator pane (e.g. RVOL with ``log_scale=True``) was
unreadable in dark mode. For a typical sub-decade RVOL ratio range the
readable y-tick labels (``2,3,4,6×10ⁿ``) are matplotlib MINOR ticks, but
``style_axes`` recoloured only majors (``tick_params`` defaults to
``which="major"``) so the minor labels stayed default-black and vanished
on the dark background.

Fix: ``style_axes`` calls ``ax.tick_params(which="both", colors=...)`` so
minor tick marks + labels follow the theme. The kwarg persists onto minor
ticks created when the render pass later calls ``set_yscale("log")``, and
recolours existing minors on a live theme swap.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from tradinglab.rendering import setup_indicator_pane_axes, style_axes  # noqa: E402

_DARK = {
    "ax_bg": "#1e1e1e",
    "text": "#e0e0e0",
    "spine": "#888888",
    "grid": "#444444",
}


def _minor_label_colors(ax) -> list:
    """Colours of every NON-empty minor y-tick label (right-side axis)."""
    out = []
    for t in ax.yaxis.get_minor_ticks():
        txt = t.label2.get_text() or t.label1.get_text()
        if txt.strip():
            # Indicator panes label on the right (label2); fall back to label1.
            out.append(t.label2.get_color() if t.label2.get_text() else t.label1.get_color())
    return out


def test_style_then_set_log_themes_minor_labels():
    """Build-time order: style_axes runs (linear), THEN set_yscale('log')."""
    fig, ax = plt.subplots(figsize=(6, 1.2))
    try:
        setup_indicator_pane_axes(ax)
        style_axes(ax, _DARK)
        ax.plot(np.arange(50), np.linspace(1.0, 9.0, 50))
        ax.set_yscale("log")
        ax.set_ylim(1.0, 9.0)
        fig.canvas.draw()
        colors = _minor_label_colors(ax)
        assert colors, "expected minor tick labels on a sub-decade log RVOL pane"
        for c in colors:
            assert c == _DARK["text"], f"minor tick label not themed in dark mode: {c!r}"
    finally:
        plt.close(fig)


def test_set_log_then_style_themes_minor_labels():
    """Theme-swap order: pane already log, THEN style_axes recolours it."""
    fig, ax = plt.subplots(figsize=(6, 1.2))
    try:
        setup_indicator_pane_axes(ax)
        ax.plot(np.arange(50), np.linspace(0.8, 4.0, 50))
        ax.set_yscale("log")
        ax.set_ylim(0.8, 4.0)
        fig.canvas.draw()
        style_axes(ax, _DARK)
        fig.canvas.draw()
        colors = _minor_label_colors(ax)
        assert colors, "expected minor tick labels on a sub-decade log RVOL pane"
        for c in colors:
            assert c == _DARK["text"], f"minor tick label not themed on swap: {c!r}"
    finally:
        plt.close(fig)


def test_major_tick_labels_still_themed():
    """Sanity: majors are themed too (the original behaviour is preserved)."""
    fig, ax = plt.subplots(figsize=(6, 1.2))
    try:
        setup_indicator_pane_axes(ax)
        style_axes(ax, _DARK)
        ax.plot(np.arange(50), np.linspace(1.0, 9.0, 50))
        ax.set_yscale("log")
        ax.set_ylim(1.0, 9.0)
        fig.canvas.draw()
        major = [
            (t.label2.get_color() if t.label2.get_text() else t.label1.get_color())
            for t in ax.yaxis.get_major_ticks()
            if (t.label2.get_text() or t.label1.get_text()).strip()
        ]
        assert major, "expected at least one major tick label"
        for c in major:
            assert c == _DARK["text"]
    finally:
        plt.close(fig)
