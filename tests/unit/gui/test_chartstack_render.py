"""Unit tests for ``gui.chartstack.render`` (post-simplification).

After the 2026-05-16 scope-shrink, ChartStack cards render only
daily OHLC candlesticks plus a small header row (symbol + last
close + %chg vs prior close). VWAP / PMH-PML / pre-post wash /
volume-stroke encoding / last-3-candles overlay / halted-symbol
treatment were all removed; the corresponding tests went with
them.

Matplotlib is forced to the Agg backend so the suite is headless.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import pytest
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

from tradinglab.gui.chartstack import render as R
from tradinglab.gui.chartstack.binding import CardBinding
from tradinglab.gui.chartstack.render import (
    apply_card_tint,
    draw_card_candles,
    draw_card_placeholder,
    draw_card_sparkline,
)
from tradinglab.gui.chartstack.series_cache import Bar

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_axes():
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    return fig, ax


def _bar(
    *,
    ts: int = 0,
    o: float = 100.0,
    h: float = 101.0,
    l: float = 99.0,
    c: float = 100.5,
    v: float = 1000.0,
) -> Bar:
    return Bar(ts=ts, open=o, high=h, low=l, close=c,
               volume=v, session="regular")


def _bull_bar(ts: int, base: float) -> Bar:
    return _bar(ts=ts, o=base, h=base + 1.0, l=base - 0.4,
                c=base + 0.7)


def _bear_bar(ts: int, base: float) -> Bar:
    return _bar(ts=ts, o=base, h=base + 0.4, l=base - 1.0,
                c=base - 0.7)


# ---------------------------------------------------------------------------
# Placeholder
# ---------------------------------------------------------------------------


def test_placeholder_writes_symbol_text():
    _fig, ax = _make_axes()
    draw_card_placeholder(ax, CardBinding(symbol="AAPL",
                                          source_label="watchlist"))
    texts = [t.get_text() for t in ax.texts]
    assert "AAPL" in texts


def test_placeholder_writes_empty_label_when_no_binding():
    _fig, ax = _make_axes()
    draw_card_placeholder(ax, None)
    texts = [t.get_text() for t in ax.texts]
    assert "(empty)" in texts


def test_placeholder_strips_ticks_and_hides_spines():
    _fig, ax = _make_axes()
    draw_card_placeholder(ax, None)
    assert list(ax.get_xticks()) == []
    assert list(ax.get_yticks()) == []
    for spine in ax.spines.values():
        assert not spine.get_visible()


# ---------------------------------------------------------------------------
# Candles — body + wick rendering
# ---------------------------------------------------------------------------


def test_candles_draw_one_rectangle_per_bar():
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="AAPL",
                                          source_label="w"))
    # Bodies are batched into a single PatchCollection for perf;
    # the underlying patch count equals the bar count.
    body_collections = [c for c in ax.collections
                        if isinstance(c, PatchCollection)]
    assert len(body_collections) == 1
    assert len(body_collections[0].get_paths()) == len(bars)


def test_candles_draw_one_wick_line_per_bar():
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="AAPL",
                                          source_label="w"))
    # Wicks are batched into a single LineCollection for perf.
    wick_collections = [c for c in ax.collections
                        if isinstance(c, LineCollection)]
    assert len(wick_collections) == 1
    assert len(wick_collections[0].get_segments()) == len(bars)


def test_candle_body_color_bull_vs_bear():
    from matplotlib.colors import to_rgba

    _fig, ax = _make_axes()
    bars = [_bull_bar(0, 100.0), _bear_bar(1, 101.0)]
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="AAPL",
                                          source_label="w"))
    body_collection = next(
        c for c in ax.collections if isinstance(c, PatchCollection))
    face_colors = body_collection.get_facecolors()
    assert len(face_colors) == 2
    assert tuple(face_colors[0]) == pytest.approx(
        tuple(to_rgba(R._UP_COLOR)), abs=1e-9)
    assert tuple(face_colors[1]) == pytest.approx(
        tuple(to_rgba(R._DOWN_COLOR)), abs=1e-9)


def test_candle_y_range_pads_above_and_below_extremes():
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="AAPL",
                                          source_label="w"))
    lows = [float(b.low) for b in bars]
    highs = [float(b.high) for b in bars]
    y0, y1 = ax.get_ylim()
    assert y0 < min(lows)
    assert y1 > max(highs)


def test_candles_strip_ticks_and_hide_spines_by_default():
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="AAPL",
                                          source_label="w"))
    assert list(ax.get_xticks()) == []
    assert list(ax.get_yticks()) == []
    for spine in ax.spines.values():
        assert not spine.get_visible()


def test_candles_doji_renders_floor_body_height():
    """Open == close → body should still be drawn (not collapsed to 0px)."""
    _fig, ax = _make_axes()
    bars = [
        _bull_bar(0, 100.0),
        _bar(ts=1, o=100.7, h=101.5, l=100.0, c=100.7),  # doji
        _bull_bar(2, 100.7),
    ]
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="AAPL",
                                          source_label="w"))
    body_collection = next(
        c for c in ax.collections if isinstance(c, PatchCollection))
    paths = body_collection.get_paths()
    assert len(paths) == 3
    # Doji path (index 1) bounding box must have positive height.
    bbox = paths[1].get_extents()
    assert bbox.height > 0.0


# ---------------------------------------------------------------------------
# Empty / single-bar fallback
# ---------------------------------------------------------------------------


def test_candles_fall_through_to_placeholder_on_single_bar():
    _fig, ax = _make_axes()
    draw_card_candles(
        ax, [_bull_bar(0, 100.0)],
        binding=CardBinding(symbol="AAPL", source_label="w"),
    )
    texts = [t.get_text() for t in ax.texts]
    # Placeholder branch writes the symbol; no candle artists.
    assert "AAPL" in texts
    assert not any(isinstance(c, PatchCollection) for c in ax.collections)


def test_candles_fall_through_to_placeholder_on_empty_bars():
    _fig, ax = _make_axes()
    draw_card_candles(
        ax, [],
        binding=CardBinding(symbol="MSFT", source_label="w"),
    )
    texts = [t.get_text() for t in ax.texts]
    assert "MSFT" in texts


# ---------------------------------------------------------------------------
# Header row
# ---------------------------------------------------------------------------


def test_candles_render_symbol_and_pct_label():
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="NVDA",
                                          source_label="w"))
    texts = [t.get_text() for t in ax.texts]
    assert "NVDA" in texts
    # %chg compares last close vs prior close — not vs first close.
    # bars[-1].close = 100.0 + 4 + 0.7 = 104.7
    # bars[-2].close = 100.0 + 3 + 0.7 = 103.7
    # pct ≈ (104.7 / 103.7 - 1) * 100 ≈ 0.96 %
    pct_label = next(
        (t for t in texts if "104.70" in t and "%" in t), None)
    assert pct_label is not None
    assert "+0.96%" in pct_label or "+0.97%" in pct_label


def test_candles_header_pct_color_tracks_today_bar_direction():
    """%chg label color encodes today's bull/bear, not the window trend."""
    from matplotlib.colors import to_rgba

    _fig, ax = _make_axes()
    # 4 up bars then a single bear bar — window is up, today is down.
    bars = [_bull_bar(i, 100.0 + i) for i in range(4)]
    bars.append(_bear_bar(4, 104.0))
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="X",
                                          source_label="w"))
    # The %chg label is the right-aligned header text.
    pct_text = next(
        t for t in ax.texts if t.get_ha() == "right")
    assert pct_text.get_color() == R._DOWN_COLOR or \
        tuple(pct_text.get_color()) == pytest.approx(
            tuple(to_rgba(R._DOWN_COLOR)), abs=1e-9)


# ---------------------------------------------------------------------------
# Tint API
# ---------------------------------------------------------------------------


def test_apply_card_tint_sets_spine_color_and_visibility():
    from matplotlib.colors import to_rgba

    _fig, ax = _make_axes()
    apply_card_tint(ax, "#ff9900")
    for spine in ax.spines.values():
        assert spine.get_visible()
        assert spine.get_edgecolor() == pytest.approx(
            to_rgba("#ff9900"), abs=1e-9)
        assert spine.get_linewidth() == pytest.approx(1.6, abs=1e-9)


def test_apply_card_tint_none_hides_spines():
    _fig, ax = _make_axes()
    apply_card_tint(ax, "#ff9900")
    apply_card_tint(ax, None)
    for spine in ax.spines.values():
        assert not spine.get_visible()


def test_apply_card_tint_is_idempotent():
    _fig, ax = _make_axes()
    apply_card_tint(ax, "#3366cc")
    apply_card_tint(ax, "#3366cc")
    for spine in ax.spines.values():
        assert spine.get_visible()
        assert spine.get_linewidth() == pytest.approx(1.6, abs=1e-9)


def test_candles_tint_kwarg_paints_spines():
    from matplotlib.colors import to_rgba

    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(
        ax, bars,
        binding=CardBinding(symbol="AAPL", source_label="w"),
        tint="#ff0000",
    )
    for spine in ax.spines.values():
        assert spine.get_visible()
        assert spine.get_edgecolor() == pytest.approx(
            to_rgba("#ff0000"), abs=1e-9)


def test_candles_tint_kwarg_paints_spines_even_on_placeholder_branch():
    _fig, ax = _make_axes()
    draw_card_candles(
        ax, [],
        binding=CardBinding(symbol="AAPL", source_label="w"),
        tint="#0000ff",
    )
    for spine in ax.spines.values():
        assert spine.get_visible()


# ---------------------------------------------------------------------------
# Backwards-compatibility shims
# ---------------------------------------------------------------------------


def test_draw_card_sparkline_is_alias_for_draw_card_candles():
    """The old name still works for any stale call sites."""
    assert draw_card_sparkline is draw_card_candles


def test_candles_swallows_legacy_overlay_kwargs():
    """Legacy ``show_*`` / ``volume_stroke_encoding`` / ``halted_at``
    kwargs must be accepted silently so the panel can keep passing
    them during the simplification rollout without crashing."""
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    # Must not raise:
    draw_card_candles(
        ax, bars,
        binding=CardBinding(symbol="AAPL", source_label="w"),
        show_vwap=True,
        show_pmh_pml=True,
        show_last_candles=True,
        volume_stroke_encoding=True,
        halted_at=2,
    )
    body_collection = next(
        c for c in ax.collections if isinstance(c, PatchCollection))
    assert len(body_collection.get_paths()) == 5


def test_render_module_public_api():
    public = set(R.__all__)
    expected = {
        "draw_card_placeholder",
        "draw_card_candles",
        "draw_card_sparkline",
        "apply_card_tint",
    }
    assert expected.issubset(public)


# ---------------------------------------------------------------------------
# Theme palette plumbing (dark-mode fix)
# ---------------------------------------------------------------------------


def _dark_palette() -> dict:
    """Sample palette matching ``constants.DARK_THEME`` shape."""
    return {
        "fig_bg": "#1e1e1e",
        "ax_bg": "#2b2b2b",
        "text": "#dcdcdc",
        "win_bg": "#1e1e1e",
    }


def _light_palette() -> dict:
    return {
        "fig_bg": "#fafafa",
        "ax_bg": "#ffffff",
        "text": "#111111",
        "win_bg": "#f0f0f0",
    }


def test_placeholder_applies_theme_text_color_when_provided():
    _fig, ax = _make_axes()
    draw_card_placeholder(
        ax,
        CardBinding(symbol="AAPL", source_label="watchlist"),
        theme=_dark_palette(),
    )
    # Symbol text is the only text artist on the placeholder.
    assert ax.texts
    assert ax.texts[0].get_color() == "#dcdcdc"


def test_placeholder_applies_theme_ax_bg_when_provided():
    _fig, ax = _make_axes()
    draw_card_placeholder(
        ax,
        CardBinding(symbol="AAPL", source_label="watchlist"),
        theme=_dark_palette(),
    )
    # matplotlib stores facecolors as RGBA tuples — compare via the hex.
    from matplotlib.colors import to_hex
    assert to_hex(ax.get_facecolor()).lower() == "#2b2b2b"


def test_placeholder_no_theme_keeps_matplotlib_defaults():
    """Tests that pre-existing fixtures still pass without theme kwargs."""
    _fig, ax = _make_axes()
    draw_card_placeholder(
        ax,
        CardBinding(symbol="AAPL", source_label="watchlist"),
    )
    # Default text color is black (matplotlib rcParams).
    from matplotlib.colors import to_hex
    assert to_hex(ax.texts[0].get_color()).lower() == "#000000"


def test_candles_apply_theme_text_color_to_symbol_header():
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(
        ax, bars,
        binding=CardBinding(symbol="AAPL", source_label="w"),
        theme=_dark_palette(),
    )
    # Header has two text artists: left-aligned symbol, right-aligned
    # last+%chg. Only the LEFT one should adopt the theme color — the
    # RIGHT one stays bull/bear-tinted.
    left = [t for t in ax.texts if t.get_ha() == "left"]
    right = [t for t in ax.texts if t.get_ha() == "right"]
    assert left and right
    assert left[0].get_color() == "#dcdcdc"
    # right (direction-encoded) — must NOT be the theme text color.
    assert right[0].get_color() != "#dcdcdc"


def test_candles_apply_theme_ax_bg():
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(
        ax, bars,
        binding=CardBinding(symbol="AAPL", source_label="w"),
        theme=_dark_palette(),
    )
    from matplotlib.colors import to_hex
    assert to_hex(ax.get_facecolor()).lower() == "#2b2b2b"


def test_candles_no_theme_keeps_matplotlib_defaults():
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(
        ax, bars,
        binding=CardBinding(symbol="AAPL", source_label="w"),
    )
    # Header symbol uses default black; %chg label uses bull color.
    from matplotlib.colors import to_hex
    left = [t for t in ax.texts if t.get_ha() == "left"]
    assert to_hex(left[0].get_color()).lower() == "#000000"


def test_candles_fall_through_propagates_theme_to_placeholder():
    """Empty-bar fall-through still respects the theme kwarg."""
    _fig, ax = _make_axes()
    draw_card_candles(
        ax, [],
        binding=CardBinding(symbol="AAPL", source_label="w"),
        theme=_dark_palette(),
    )
    # Falls through to placeholder; placeholder text should be themed.
    assert ax.texts
    assert ax.texts[0].get_color() == "#dcdcdc"


def test_candles_theme_light_to_dark_swap_idempotent():
    """Calling draw twice with different themes ends in the latter."""
    _fig, ax = _make_axes()
    bars = [_bull_bar(i, 100.0 + i) for i in range(5)]
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="AAPL", source_label="w"),
                      theme=_light_palette())
    draw_card_candles(ax, bars,
                      binding=CardBinding(symbol="AAPL", source_label="w"),
                      theme=_dark_palette())
    from matplotlib.colors import to_hex
    left = [t for t in ax.texts if t.get_ha() == "left"]
    assert left[0].get_color() == "#dcdcdc"
    assert to_hex(ax.get_facecolor()).lower() == "#2b2b2b"


def test_render_theme_helpers_tolerate_missing_keys():
    """Palette missing ``text`` / ``ax_bg`` keys must not crash."""
    _fig, ax = _make_axes()
    # Partial palette: only fig_bg present.
    draw_card_placeholder(
        ax,
        CardBinding(symbol="AAPL", source_label="w"),
        theme={"fig_bg": "#000000"},
    )
    from matplotlib.colors import to_hex
    # No text key → matplotlib default black.
    assert to_hex(ax.texts[0].get_color()).lower() == "#000000"
    # No ax_bg key → matplotlib default white.
    assert to_hex(ax.get_facecolor()).lower() == "#ffffff"


def test_render_theme_helpers_tolerate_non_string_values():
    """Non-string palette values must be ignored, not propagated."""
    _fig, ax = _make_axes()
    draw_card_placeholder(
        ax,
        CardBinding(symbol="AAPL", source_label="w"),
        theme={"text": None, "ax_bg": 42},  # type: ignore[dict-item]
    )
    from matplotlib.colors import to_hex
    assert to_hex(ax.texts[0].get_color()).lower() == "#000000"
    assert to_hex(ax.get_facecolor()).lower() == "#ffffff"
