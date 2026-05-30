"""Bit-equivalence tests for the vectorised chandelier ratchet helpers.

``_ratchet_long`` / ``_ratchet_short`` were per-bar Python loops computing a
NaN-aware running max/min seeded with an optional ``prev`` value. These tests
pin the vectorised replacements against inline scalar reference loops that
mirror the original code.
"""

from __future__ import annotations

import numpy as np
import pytest

import tradinglab.indicators  # noqa: F401  (resolve chandelier_math import order)
from tradinglab.core.chandelier_math import _ratchet_long, _ratchet_short


def _ref_ratchet_long(arr: np.ndarray, prev: float | None) -> np.ndarray:
    out = arr.copy()
    running: float | None = prev
    for i in range(out.size):
        v = out[i]
        if not np.isfinite(v):
            continue
        if running is None or v > running:
            running = float(v)
        out[i] = running
    return out


def _ref_ratchet_short(arr: np.ndarray, prev: float | None) -> np.ndarray:
    out = arr.copy()
    running: float | None = prev
    for i in range(out.size):
        v = out[i]
        if not np.isfinite(v):
            continue
        if running is None or v < running:
            running = float(v)
        out[i] = running
    return out


def _make_series(rng: np.random.Generator, n: int) -> np.ndarray:
    if n == 0:
        return np.empty(0, dtype=np.float64)
    a = rng.normal(100.0, 10.0, size=n).astype(np.float64)
    # leading NaN warm-up + interior gaps
    lead = int(rng.integers(0, max(1, n // 4) + 1))
    a[:lead] = np.nan
    gaps = rng.integers(0, n, size=max(0, n // 8))
    a[gaps] = np.nan
    return a


@pytest.mark.parametrize("prev", [None, 95.0, 105.0, 50.0, 200.0])
@pytest.mark.parametrize("n", [0, 1, 5, 64, 5_000])
def test_ratchet_long_matches_reference(prev, n: int) -> None:
    rng = np.random.default_rng(1000 + n + int(prev or 0))
    for _ in range(5):
        arr = _make_series(rng, n)
        got = _ratchet_long(arr, prev)
        ref = _ref_ratchet_long(arr, prev)
        assert np.array_equal(np.isnan(got), np.isnan(ref))
        m = ~np.isnan(ref)
        if m.any():
            np.testing.assert_allclose(got[m], ref[m], rtol=0, atol=0)


@pytest.mark.parametrize("prev", [None, 95.0, 105.0, 50.0, 200.0])
@pytest.mark.parametrize("n", [0, 1, 5, 64, 5_000])
def test_ratchet_short_matches_reference(prev, n: int) -> None:
    rng = np.random.default_rng(2000 + n + int(prev or 0))
    for _ in range(5):
        arr = _make_series(rng, n)
        got = _ratchet_short(arr, prev)
        ref = _ref_ratchet_short(arr, prev)
        assert np.array_equal(np.isnan(got), np.isnan(ref))
        m = ~np.isnan(ref)
        if m.any():
            np.testing.assert_allclose(got[m], ref[m], rtol=0, atol=0)


def test_ratchet_all_nan_passthrough() -> None:
    arr = np.full(8, np.nan)
    assert np.all(np.isnan(_ratchet_long(arr, None)))
    assert np.all(np.isnan(_ratchet_short(arr, 100.0)))


def test_ratchet_does_not_mutate_input() -> None:
    arr = np.array([np.nan, 3.0, 1.0, 5.0, np.nan, 2.0], dtype=np.float64)
    snapshot = arr.copy()
    _ratchet_long(arr, None)
    _ratchet_short(arr, None)
    assert np.array_equal(np.isnan(arr), np.isnan(snapshot))
    m = ~np.isnan(snapshot)
    assert np.array_equal(arr[m], snapshot[m])
