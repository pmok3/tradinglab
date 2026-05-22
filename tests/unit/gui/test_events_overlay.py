"""Unit tests for chart event text-glyph rendering."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.text import Text

from tradinglab.events.render import (
    GLYPH_DIVIDEND,
    GLYPH_EARNINGS_FORWARD,
    GLYPH_EARNINGS_PAST,
    EventGlyph,
)
from tradinglab.gui.events_overlay import draw_event_glyphs


def test_draw_event_glyphs_uses_text_letters_not_line_markers():
    fig, ax = plt.subplots()
    glyphs = [
        EventGlyph(1, GLYPH_EARNINGS_PAST, "Earnings AMC", 1, marker_glyph="A"),
        EventGlyph(2, GLYPH_EARNINGS_FORWARD, "Earnings BMO", 2, marker_glyph="B"),
        EventGlyph(3, GLYPH_DIVIDEND, "Dividend", 3, marker_glyph="D"),
    ]
    try:
        payload = draw_event_glyphs(
            ax,
            glyphs,
            offset=0,
            theme={
                "tooltip_fg": "#eeeeee",
                "tooltip_bg": "#111111",
                "spine": "#333333",
            },
        )
        assert [artist.get_text() for artist in payload.artists] == ["A", "B", "D"]
        assert all(isinstance(artist, Text) for artist in payload.artists)
        assert len(ax.lines) == 0
        assert [text.get_text() for text in ax.texts] == ["A", "B", "D"]
        assert all(text.get_bbox_patch() is not None for text in payload.artists)
        assert payload.hit_meta == [
            (1.0, GLYPH_EARNINGS_PAST, "Earnings AMC"),
            (2.0, GLYPH_EARNINGS_FORWARD, "Earnings BMO"),
            (3.0, GLYPH_DIVIDEND, "Dividend"),
        ]
        assert payload.artists[0].get_color() == "#eeeeee"
    finally:
        plt.close(fig)
