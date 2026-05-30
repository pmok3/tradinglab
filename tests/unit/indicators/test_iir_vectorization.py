"""Bit-equivalence tests for the vectorised IIR kernel and its consumers.

Each test pins the new vectorised implementation against an inline scalar
reference loop that mirrors the *original* per-bar code, across random and
edge-case inputs. The contract is "identical output within float64
round-off", so these guard the perf refactor from silently changing
indicator values.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradinglab.indicators._iir import (
    ema_first_seeded_nan,
    ema_sma_seeded,
    iir_tail,
)

# --- reference scalar implementations (mirror the pre-refactor code) ----


def _ref_iir_tail(tail_b: np.ndarray, q: float, seed: float) -> np.ndarray:
    out = np.empty(tail_b.size, dtype=np.float64)
    prev = float(seed)
    for k in range(tail_b.size):
        prev = q * prev + float(tail_b[k])
        out[k] = prev
    return out


def _ref_ema_sma_seeded(arr: np.ndarray, length: int) -> np.ndarray:
    out = np.full_like(arr, np.nan, dtype=np.float64)
    n = arr.size
    if n == 0 or length < 1:
        return out
    mask = np.isfinite(arr)
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        return out
    first = int(indices[0])
    if first + length > n:
        return out
    alpha = 2.0 / (length + 1.0)
    seed_end = first + length
    window = arr[first:seed_end]
    cleaned_seed = np.where(np.isfinite(window), window, 0.0)
    seed = float(cleaned_seed.mean())
    out[seed_end - 1] = seed
    prev = seed
    for i in range(seed_end, n):
        v = arr[i]
        if not np.isfinite(v):
            v = 0.0
        prev = alpha * float(v) + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _ref_ema_with_nan(arr: np.ndarray, length: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    if arr.size == 0:
        return out
    alpha = 2.0 / (length + 1.0)
    seeded = False
    prev = 0.0
    for i in range(arr.size):
        v = arr[i]
        if not np.isfinite(v):
            continue
        if not seeded:
            prev = float(v)
            seeded = True
            out[i] = prev
            continue
        prev = alpha * float(v) + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _assert_equiv(a: np.ndarray, b: np.ndarray) -> None:
    assert a.shape == b.shape
    na, nb = np.isnan(a), np.isnan(b)
    assert np.array_equal(na, nb), "NaN masks differ"
    np.testing.assert_allclose(a[~na], b[~nb], rtol=1e-9, atol=1e-9)


# --- iir_tail core ------------------------------------------------------


@pytest.mark.parametrize("q", [0.0, 0.05, 0.5, 0.8, 0.9, 0.99, 0.999])
@pytest.mark.parametrize("m", [0, 1, 2, 5, 50, 5000, 100_000])
def test_iir_tail_matches_scalar(q: float, m: int) -> None:
    rng = np.random.default_rng(7)
    tail = rng.normal(0.0, 1.0, size=m).astype(np.float64)
    seed = 3.14
    got = iir_tail(tail, q, seed)
    ref = _ref_iir_tail(tail, q, seed)
    _assert_equiv(got, ref)


def test_iir_tail_q_zero_is_passthrough() -> None:
    tail = np.array([1.0, -2.0, 3.5], dtype=np.float64)
    np.testing.assert_array_equal(iir_tail(tail, 0.0, 99.0), tail)


def test_iir_tail_chunk_boundary_chaining() -> None:
    # q close to 1 forces small chunks, exercising the chunk-chaining path.
    rng = np.random.default_rng(11)
    tail = rng.normal(size=20_000).astype(np.float64)
    for q in (0.995, 0.9995):
        _assert_equiv(iir_tail(tail, q, 0.0), _ref_iir_tail(tail, q, 0.0))


# --- ema_sma_seeded (ma_kernels.ema) ------------------------------------


@pytest.mark.parametrize("length", [1, 2, 9, 21, 200])
@pytest.mark.parametrize("n", [0, 1, 5, 250, 25_000])
def test_ema_sma_seeded_matches_reference(length: int, n: int) -> None:
    rng = np.random.default_rng(length + n)
    arr = rng.normal(100.0, 5.0, size=n).astype(np.float64)
    _assert_equiv(ema_sma_seeded(arr, length), _ref_ema_sma_seeded(arr, length))


def test_ema_sma_seeded_with_leading_and_midstream_nan() -> None:
    arr = np.array([np.nan, np.nan, 1.0, 2.0, np.nan, 4.0, 5.0, 6.0, 7.0])
    for length in (1, 2, 3):
        _assert_equiv(
            ema_sma_seeded(arr, length), _ref_ema_sma_seeded(arr, length)
        )


# --- ema_first_seeded_nan (smi._ema_with_nan) ---------------------------


@pytest.mark.parametrize("length", [1, 3, 10, 25])
@pytest.mark.parametrize("n", [0, 1, 4, 300, 25_000])
def test_ema_first_seeded_nan_matches_reference(length: int, n: int) -> None:
    rng = np.random.default_rng(length * 100 + n)
    arr = rng.normal(0.0, 2.0, size=n).astype(np.float64)
    _assert_equiv(
        ema_first_seeded_nan(arr, length), _ref_ema_with_nan(arr, length)
    )


def test_ema_first_seeded_nan_with_gaps() -> None:
    arr = np.array([np.nan, 1.0, 2.0, np.nan, np.nan, 5.0, 6.0, np.nan, 8.0])
    for length in (1, 2, 5):
        _assert_equiv(
            ema_first_seeded_nan(arr, length), _ref_ema_with_nan(arr, length)
        )


def test_ema_first_seeded_nan_all_nan() -> None:
    arr = np.full(10, np.nan)
    out = ema_first_seeded_nan(arr, 5)
    assert np.isnan(out).all()


# --- macd.classify_histogram --------------------------------------------


def _ref_classify_histogram(hist: np.ndarray) -> np.ndarray:
    n = hist.shape[0]
    out = np.full(n, -1, dtype=np.int8)
    if n == 0:
        return out
    finite = np.isfinite(hist)
    if not finite.any():
        return out
    first = int(np.argmax(finite))
    prev = hist[first]
    out[first] = 0 if prev > 0 else 2
    for i in range(first + 1, n):
        v = hist[i]
        if not np.isfinite(v):
            prev = v
            continue
        rising = (v >= prev) if np.isfinite(prev) else True
        if v > 0:
            out[i] = 0 if rising else 1
        else:
            out[i] = 2 if rising else 3
        prev = v
    return out


def _make_hist(rng: np.random.Generator, n: int) -> np.ndarray:
    if n == 0:
        return np.empty(0, dtype=np.float64)
    h = rng.normal(0.0, 1.0, size=n).astype(np.float64)
    # sprinkle leading + interior NaN gaps
    k = rng.integers(0, max(1, n // 5) + 1)
    if k:
        h[: int(k)] = np.nan
    gaps = rng.integers(0, n, size=max(0, n // 10))
    h[gaps] = np.nan
    return h


@pytest.mark.parametrize("n", [0, 1, 2, 5, 64, 5_000])
def test_classify_histogram_matches_reference(n: int) -> None:
    from tradinglab.indicators.macd import classify_histogram

    rng = np.random.default_rng(7_000 + n)
    for _ in range(6):
        hist = _make_hist(rng, n)
        got = classify_histogram(hist)
        ref = _ref_classify_histogram(hist)
        assert got.dtype == ref.dtype == np.int8
        assert np.array_equal(got, ref)


def test_classify_histogram_all_nan_and_flat() -> None:
    from tradinglab.indicators.macd import classify_histogram

    allnan = np.full(10, np.nan)
    assert np.array_equal(classify_histogram(allnan), _ref_classify_histogram(allnan))
    flat = np.zeros(10, dtype=np.float64)  # hist == 0 => not above, equal => rising
    assert np.array_equal(classify_histogram(flat), _ref_classify_histogram(flat))


# --- lrsi.compute_arr (4-stage Laguerre cascade) ------------------------


def _ref_lrsi(prices: np.ndarray, gamma: float) -> np.ndarray:
    n = prices.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return out
    L0 = float(prices[0])
    L1 = L0
    L2 = L0
    L3 = L0
    for i in range(n):
        p = float(prices[i])
        if not np.isfinite(p):
            continue
        L0n = (1.0 - gamma) * p + gamma * L0
        L1n = -gamma * L0n + L0 + gamma * L1
        L2n = -gamma * L1n + L1 + gamma * L2
        L3n = -gamma * L2n + L2 + gamma * L3
        L0, L1, L2, L3 = L0n, L1n, L2n, L3n
        cu = 0.0
        cd = 0.0
        if L0 >= L1:
            cu += L0 - L1
        else:
            cd += L1 - L0
        if L1 >= L2:
            cu += L1 - L2
        else:
            cd += L2 - L1
        if L2 >= L3:
            cu += L2 - L3
        else:
            cd += L3 - L2
        denom = cu + cd
        if i < 3:
            continue
        out[i] = 50.0 if denom <= 0.0 else 100.0 * (cu / denom)
    return out


class _BarsStub:
    def __init__(self, close: np.ndarray) -> None:
        self.close = close

    def __len__(self) -> int:
        return self.close.shape[0]


@pytest.mark.parametrize("gamma", [0.1, 0.5, 0.7, 0.9])
@pytest.mark.parametrize("n", [0, 1, 3, 4, 50, 5_000])
def test_lrsi_matches_reference(gamma: float, n: int) -> None:
    from tradinglab.indicators.lrsi import LRSI

    rng = np.random.default_rng(int(gamma * 1000) + n)
    prices = rng.normal(100.0, 3.0, size=n).astype(np.float64) if n else np.empty(0)
    ind = LRSI(gamma=gamma)
    got = ind.compute_arr(_BarsStub(prices.copy()))["lrsi"]
    ref = _ref_lrsi(prices, gamma)
    assert got.shape == ref.shape
    assert np.array_equal(np.isnan(got), np.isnan(ref))
    m = ~np.isnan(ref)
    if m.any():
        np.testing.assert_allclose(got[m], ref[m], rtol=1e-9, atol=1e-9)


def test_lrsi_interior_and_leading_nan() -> None:
    from tradinglab.indicators.lrsi import LRSI

    rng = np.random.default_rng(321)
    prices = rng.normal(50.0, 2.0, size=200).astype(np.float64)
    prices[5] = np.nan
    prices[6] = np.nan
    prices[100] = np.nan
    ind = LRSI(gamma=0.6)
    got = ind.compute_arr(_BarsStub(prices.copy()))["lrsi"]
    ref = _ref_lrsi(prices, 0.6)
    assert np.array_equal(np.isnan(got), np.isnan(ref))
    m = ~np.isnan(ref)
    np.testing.assert_allclose(got[m], ref[m], rtol=1e-9, atol=1e-9)


def test_lrsi_nonfinite_first_price_poisons_series() -> None:
    from tradinglab.indicators.lrsi import LRSI

    prices = np.array([np.nan, 1.0, 2.0, 3.0, 4.0, 5.0], dtype=np.float64)
    ind = LRSI(gamma=0.5)
    got = ind.compute_arr(_BarsStub(prices.copy()))["lrsi"]
    ref = _ref_lrsi(prices, 0.5)
    assert np.array_equal(np.isnan(got), np.isnan(ref))
    assert np.all(np.isnan(got))
