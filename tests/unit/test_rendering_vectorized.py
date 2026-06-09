"""Bit-for-bit equivalence: vectorized candle/volume geometry vs the
per-bar ``bar_geometry`` / ``vol_geometry`` reference.

The render hot path (``draw_candlesticks`` non-hollow + ``draw_volume``)
builds wick/body/volume vertices and colours with numpy instead of a
per-bar Python loop (perf sprint #2). These tests pin that the produced
geometry + colours + ``_sc_*`` caches are byte-identical to looping the
single-bar helpers, so journal screenshots / on-screen candles never
drift. Also asserts the cache dtypes the H1 tick fastpath + volume-TOD
suppression rely on (numpy ``_sc_verts``, list ``_sc_colors`` /
``_sc_src_indices``).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tradinglab.models import Candle  # noqa: E402
from tradinglab.rendering import (  # noqa: E402
    _BODY_HALF,
    bar_geometry,
    draw_candlesticks,
    draw_volume,
    vol_geometry,
)


def _make(n, *, extended_every=0, gap_every=0, doji_every=0):
    base = datetime(2024, 3, 4, 9, 30)
    rng = np.random.default_rng(11)
    out = []
    price = 100.0
    for i in range(n):
        if gap_every and i % gap_every == 0:
            out.append(Candle.gap(base + timedelta(minutes=5 * i)))
            continue
        o = price
        c = price + float(rng.normal(0, 1.0))
        hi = max(o, c) + abs(float(rng.normal(0, 0.5)))
        lo = min(o, c) - abs(float(rng.normal(0, 0.5)))
        if doji_every and i % doji_every == 0:
            o = c = hi = lo = price
        sess = "regular"
        if extended_every and i % extended_every == 0:
            sess = "pre" if i % (2 * extended_every) == 0 else "post"
        out.append(Candle(date=base + timedelta(minutes=5 * i), open=o, high=hi,
                          low=lo, close=c, volume=1000 + i * 3, session=sess))
        price = c
    return out


def _ref_candles(candles, x_offset, start, end, body_half):
    wsegs, bverts, colors, src = [], [], [], []
    for i in range(start, end):
        c = candles[i]
        if c.is_gap:
            continue
        ws, bv, col = bar_geometry(c, i + x_offset, body_half=body_half)
        wsegs.append(ws)
        bverts.append(bv)
        colors.append(col)
        src.append(i)
    return wsegs, bverts, colors, src


def _ref_vol(candles, x_offset, start, end, body_half):
    polys, colors, src = [], [], []
    for i in range(start, end):
        c = candles[i]
        if c.is_gap:
            continue
        v, col = vol_geometry(c, i + x_offset, body_half=body_half)
        polys.append(v)
        colors.append(col)
        src.append(i)
    return polys, colors, src


@pytest.mark.parametrize(
    "kw",
    [
        dict(n=40),
        dict(n=40, extended_every=4),
        dict(n=40, gap_every=7),
        dict(n=40, doji_every=5),
        dict(n=60, extended_every=4, gap_every=7, doji_every=5),
        dict(n=1),
    ],
)
@pytest.mark.parametrize("x_offset,body_half", [(0, None), (5, 0.3), (-3, 0.05)])
def test_candle_geometry_bit_identical(kw, x_offset, body_half):
    candles = _make(**kw)
    bh = _BODY_HALF if body_half is None else body_half
    fig, ax = plt.subplots()
    try:
        wicks, bodies = draw_candlesticks(
            ax, candles, x_offset=x_offset, start=0, end=len(candles),
            body_half=body_half,
        )
        rseg, rbv, rcol, rsrc = _ref_candles(candles, x_offset, 0, len(candles), bh)
        if not rsrc:
            assert wicks is None and bodies is None
            return
        assert list(bodies._sc_src_indices) == rsrc
        assert isinstance(bodies._sc_src_indices, list)
        assert isinstance(bodies._sc_colors, list)
        assert isinstance(bodies._sc_verts, np.ndarray)
        assert isinstance(wicks._sc_segments, np.ndarray)
        assert np.array_equal(np.asarray(bodies._sc_verts, float),
                              np.array(rbv, dtype=float))
        assert np.array_equal(np.asarray(wicks._sc_segments, float),
                              np.array(rseg, dtype=float))
        assert np.array_equal(np.array(bodies._sc_colors, float),
                              np.array(rcol, dtype=float))
        # The artist's own facecolors match too.
        assert np.array_equal(np.asarray(bodies.get_facecolors(), float),
                              np.array(rcol, dtype=float))
    finally:
        plt.close(fig)


@pytest.mark.parametrize(
    "kw",
    [dict(n=40), dict(n=40, extended_every=4), dict(n=40, gap_every=7), dict(n=1)],
)
@pytest.mark.parametrize("x_offset,body_half", [(0, None), (5, 0.3)])
def test_volume_geometry_bit_identical(kw, x_offset, body_half):
    candles = _make(**kw)
    bh = _BODY_HALF if body_half is None else body_half
    fig, ax = plt.subplots()
    try:
        bars = draw_volume(
            ax, candles, x_offset=x_offset, start=0, end=len(candles),
            body_half=body_half,
        )
        rpoly, rcol, rsrc = _ref_vol(candles, x_offset, 0, len(candles), bh)
        if not rsrc:
            assert bars is None
            return
        assert list(bars._sc_src_indices) == rsrc
        assert isinstance(bars._sc_colors, list)
        assert isinstance(bars._sc_verts, np.ndarray)
        assert np.array_equal(np.asarray(bars._sc_verts, float),
                              np.array(rpoly, dtype=float))
        assert np.array_equal(np.array(bars._sc_colors, float),
                              np.array(rcol, dtype=float))
    finally:
        plt.close(fig)


def test_subslice_skips_gaps_and_offsets():
    candles = _make(50, gap_every=6, extended_every=4)
    fig, ax = plt.subplots()
    try:
        wicks, bodies = draw_candlesticks(ax, candles, x_offset=2, start=10, end=30)
        rseg, rbv, rcol, rsrc = _ref_candles(candles, 2, 10, 30, _BODY_HALF)
        assert list(bodies._sc_src_indices) == rsrc
        assert np.array_equal(np.asarray(bodies._sc_verts, float),
                              np.array(rbv, dtype=float))
    finally:
        plt.close(fig)


def test_numpy_vert_cache_supports_fastpath_tick_mutation():
    # The H1 tick fastpath does ``bodies._sc_verts[-1] = <(4,2) tuple>``
    # then ``set_verts``. Ensure the numpy cache supports that.
    candles = _make(20)
    fig, ax = plt.subplots()
    try:
        _wicks, bodies = draw_candlesticks(ax, candles, start=0, end=len(candles))
        new_body = ((19.0, 1.0), (19.0, 2.0), (19.3, 2.0), (19.3, 1.0))
        bodies._sc_verts[-1] = new_body
        bodies.set_verts(bodies._sc_verts)
        last = bodies.get_paths()[-1].vertices
        assert np.allclose(last[:4], np.array(new_body))
        # list colour cache supports item assignment (fastpath rewrites [-1])
        bodies._sc_colors[-1] = (0.0, 0.0, 0.0, 1.0)
        bodies.set_facecolors(bodies._sc_colors)
    finally:
        plt.close(fig)


def test_all_gap_slice_returns_none():
    candles = [Candle.gap(datetime(2024, 3, 4) + timedelta(minutes=5 * i)) for i in range(8)]
    fig, ax = plt.subplots()
    try:
        wicks, bodies = draw_candlesticks(ax, candles, start=0, end=8)
        assert wicks is None and bodies is None
        bars = draw_volume(ax, candles, start=0, end=8)
        assert bars is None
    finally:
        plt.close(fig)
