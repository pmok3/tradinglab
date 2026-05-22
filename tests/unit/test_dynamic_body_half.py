"""Unit tests for ``rendering.dynamic_body_half`` and ``bar_geometry``'s
``body_half`` parameter — bug A5 (no candle-width auto-adjust at extreme
zoom-out).

The legacy ``_BODY_WIDTH = 0.6`` (half = 0.3 data units) is wider than
the bar's data-axis step (1.0) at zooms below ≈ 3 px/bar, so adjacent
bars' bodies visibly overlap. The fix: at dense zooms the body half
scales DOWN with px-per-bar, clamped to a hairline floor that's still
visible.

These tests pin:
* The width invariant — body half never exceeds the legacy default.
* The taper — at extreme zoom-out the half shrinks below default.
* The floor — never collapses to zero (would visually disappear).
* The defensive fallback — unrealized axes (bbox.width <= 1) returns
  the legacy default rather than crashing or zeroing.
* The geometry consumer — ``bar_geometry(c, x, body_half=h)`` actually
  applies ``h`` (the body's left edge is at ``x - h``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab.models import Candle
from tradinglab.rendering import (
    _BODY_HALF,
    _BODY_HALF_FLOOR,
    _DENSE_PX_PER_BAR_THRESHOLD,
    bar_geometry,
    dynamic_body_half,
    vol_geometry,
)


def _fake_ax(width_px: float):
    """Mock axes that exposes only the ``bbox.width`` attribute used by
    :func:`dynamic_body_half`. Avoids the matplotlib import cost.
    """
    return SimpleNamespace(bbox=SimpleNamespace(width=width_px))


def _mk_candle(o=10.0, h=11.0, l=9.0, c=10.5, v=1000):
    from datetime import datetime, timezone
    return Candle(
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        open=o, high=h, low=l, close=c, volume=v,
    )


# --- dynamic_body_half --------------------------------------------------

def test_dynamic_body_half_dense_returns_legacy_default():
    # 1000 px / 10 bars = 100 px/bar — well above the dense threshold.
    ax = _fake_ax(1000.0)
    assert dynamic_body_half(ax, 10) == pytest.approx(_BODY_HALF)


def test_dynamic_body_half_at_threshold_returns_legacy():
    # Exactly at threshold — return default.
    ax = _fake_ax(_DENSE_PX_PER_BAR_THRESHOLD)
    assert dynamic_body_half(ax, 1) == pytest.approx(_BODY_HALF)


def test_dynamic_body_half_extreme_zoom_out_scales_down():
    # 100 px / 50 bars = 2 px/bar — below the 4 px/bar threshold.
    ax = _fake_ax(100.0)
    half = dynamic_body_half(ax, 50)
    assert half < _BODY_HALF
    assert half >= _BODY_HALF_FLOOR


def test_dynamic_body_half_extreme_zoom_clamped_to_floor():
    # 100 px / 5000 bars ≈ 0.02 px/bar — would compute below the floor.
    ax = _fake_ax(100.0)
    half = dynamic_body_half(ax, 5000)
    assert half == pytest.approx(_BODY_HALF_FLOOR)


def test_dynamic_body_half_unrealized_axes_returns_default():
    # Common case: axes hasn't been laid out yet, bbox.width is 1.
    ax = _fake_ax(1.0)
    assert dynamic_body_half(ax, 50) == pytest.approx(_BODY_HALF)


def test_dynamic_body_half_zero_bars_returns_default():
    ax = _fake_ax(800.0)
    assert dynamic_body_half(ax, 0) == pytest.approx(_BODY_HALF)


def test_dynamic_body_half_negative_bars_returns_default():
    ax = _fake_ax(800.0)
    assert dynamic_body_half(ax, -3) == pytest.approx(_BODY_HALF)


def test_dynamic_body_half_handles_broken_bbox():
    # bbox attr missing — defensive try/except returns default.
    ax = SimpleNamespace()
    assert dynamic_body_half(ax, 50) == pytest.approx(_BODY_HALF)


def test_dynamic_body_half_handles_nonnumeric_width():
    class BadBbox:
        @property
        def width(self):
            raise RuntimeError("layout-thread race")
    ax = SimpleNamespace(bbox=BadBbox())
    assert dynamic_body_half(ax, 50) == pytest.approx(_BODY_HALF)


# --- bar_geometry ``body_half`` parameter -------------------------------

def test_bar_geometry_default_body_half_matches_legacy():
    c = _mk_candle()
    wick, body, _color = bar_geometry(c, 5.0)
    # Body is at x ± _BODY_HALF
    assert body[0][0] == pytest.approx(5.0 - _BODY_HALF)
    assert body[2][0] == pytest.approx(5.0 + _BODY_HALF)


def test_bar_geometry_custom_body_half_used():
    c = _mk_candle()
    _wick, body, _color = bar_geometry(c, 5.0, body_half=0.1)
    assert body[0][0] == pytest.approx(4.9)
    assert body[2][0] == pytest.approx(5.1)


def test_bar_geometry_floor_body_half_still_nonempty():
    c = _mk_candle()
    _wick, body, _color = bar_geometry(c, 5.0, body_half=_BODY_HALF_FLOOR)
    width = body[2][0] - body[0][0]
    assert width == pytest.approx(2 * _BODY_HALF_FLOOR)
    assert width > 0


def test_bar_geometry_wick_unaffected_by_body_half():
    c = _mk_candle(o=10.0, h=12.0, l=8.0, c=11.0)
    wick_default, _b, _co = bar_geometry(c, 5.0)
    wick_thin, _b, _co = bar_geometry(c, 5.0, body_half=0.05)
    assert wick_default == wick_thin


# --- vol_geometry ``body_half`` parameter -------------------------------

def test_vol_geometry_custom_body_half_used():
    c = _mk_candle()
    verts, _color = vol_geometry(c, 7.0, body_half=0.1)
    assert verts[0][0] == pytest.approx(6.9)
    assert verts[2][0] == pytest.approx(7.1)


def test_vol_geometry_default_body_half_matches_legacy():
    c = _mk_candle()
    verts, _color = vol_geometry(c, 7.0)
    assert verts[0][0] == pytest.approx(7.0 - _BODY_HALF)
    assert verts[2][0] == pytest.approx(7.0 + _BODY_HALF)
