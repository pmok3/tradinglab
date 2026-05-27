"""Focused tests for the safe-scalar helpers in ``scanner.fields``.

Pins the contract of ``_scalar_at`` + ``_two_finite`` (the canonical
single-bar / two-bar guards used by ~30 scanner builtins) and the
three back-compat thin wrappers (``_at``, ``_kb_at_int8``,
``_kb_at_int64``).
"""

from __future__ import annotations

import numpy as np

from tradinglab.scanner.fields import (
    _at,
    _kb_at_int8,
    _kb_at_int64,
    _scalar_at,
    _two_finite,
)

# ---------------------------------------------------------------------------
# _scalar_at
# ---------------------------------------------------------------------------


def test_scalar_at_in_bounds_returns_float() -> None:
    arr = np.array([1.0, 2.5, 3.0])
    assert _scalar_at(arr, 1) == 2.5
    assert isinstance(_scalar_at(arr, 1), float)


def test_scalar_at_negative_index_returns_none() -> None:
    arr = np.array([1.0, 2.0])
    assert _scalar_at(arr, -1) is None


def test_scalar_at_oob_index_returns_none() -> None:
    arr = np.array([1.0, 2.0])
    assert _scalar_at(arr, 2) is None
    assert _scalar_at(arr, 99) is None


def test_scalar_at_empty_array_returns_none() -> None:
    arr = np.array([], dtype=float)
    assert _scalar_at(arr, 0) is None


def test_scalar_at_nan_returns_none() -> None:
    arr = np.array([1.0, np.nan, 3.0])
    assert _scalar_at(arr, 1) is None


def test_scalar_at_pos_inf_returns_none() -> None:
    arr = np.array([1.0, np.inf, 3.0])
    assert _scalar_at(arr, 1) is None


def test_scalar_at_neg_inf_returns_none() -> None:
    arr = np.array([1.0, -np.inf, 3.0])
    assert _scalar_at(arr, 1) is None


def test_scalar_at_int8_sentinel_returns_none() -> None:
    arr = np.array([1, -128, 2], dtype=np.int8)
    assert _scalar_at(arr, 0) == 1.0
    assert _scalar_at(arr, 1, sentinel=-128) is None
    assert _scalar_at(arr, 2, sentinel=-128) == 2.0


def test_scalar_at_int64_sentinel_predicate_returns_none() -> None:
    arr = np.array([5, -1, 0, 7], dtype=np.int64)

    def pred(v: object) -> bool:
        return v < 0

    assert _scalar_at(arr, 0, sentinel_predicate=pred) == 5.0
    assert _scalar_at(arr, 1, sentinel_predicate=pred) is None
    # Zero must not match a "< 0" predicate.
    assert _scalar_at(arr, 2, sentinel_predicate=pred) == 0.0
    assert _scalar_at(arr, 3, sentinel_predicate=pred) == 7.0


def test_scalar_at_sentinel_takes_precedence_over_finite_check() -> None:
    # Sentinel hit short-circuits before the isfinite check would have
    # accepted the value (predictable for callers that pass integer
    # arrays where every value is "finite").
    arr = np.array([0, 0, -128, 0], dtype=np.int8)
    assert _scalar_at(arr, 2, sentinel=-128) is None
    assert _scalar_at(arr, 0, sentinel=-128) == 0.0


# ---------------------------------------------------------------------------
# _two_finite
# ---------------------------------------------------------------------------


def test_two_finite_both_finite_returns_tuple() -> None:
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([10.0, 20.0, 30.0])
    result = _two_finite(a, b, 1)
    assert result == (2.0, 20.0)
    assert isinstance(result[0], float) and isinstance(result[1], float)


def test_two_finite_first_nan_returns_none() -> None:
    a = np.array([1.0, np.nan, 3.0])
    b = np.array([10.0, 20.0, 30.0])
    assert _two_finite(a, b, 1) is None


def test_two_finite_second_nan_returns_none() -> None:
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([10.0, np.nan, 30.0])
    assert _two_finite(a, b, 1) is None


def test_two_finite_inf_returns_none() -> None:
    a = np.array([1.0, np.inf, 3.0])
    b = np.array([10.0, 20.0, 30.0])
    assert _two_finite(a, b, 1) is None


def test_two_finite_negative_index_returns_none() -> None:
    a = np.array([1.0, 2.0])
    b = np.array([3.0, 4.0])
    assert _two_finite(a, b, -1) is None


def test_two_finite_oob_on_first_returns_none() -> None:
    a = np.array([1.0])
    b = np.array([10.0, 20.0, 30.0])
    assert _two_finite(a, b, 1) is None


def test_two_finite_oob_on_second_returns_none() -> None:
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([10.0])
    assert _two_finite(a, b, 1) is None


# ---------------------------------------------------------------------------
# Back-compat wrappers preserve their original signature + behaviour
# ---------------------------------------------------------------------------


def test_at_back_compat_basic() -> None:
    arr = np.array([1.0, 2.5, 3.0])
    assert _at(arr, 1) == 2.5
    assert _at(arr, -1) is None
    assert _at(arr, 99) is None


def test_at_back_compat_nan() -> None:
    arr = np.array([1.0, np.nan, 3.0])
    assert _at(arr, 1) is None


def test_kb_at_int8_back_compat() -> None:
    arr = np.array([1, -1, -128, 0], dtype=np.int8)
    assert _kb_at_int8(arr, 0) == 1.0
    assert _kb_at_int8(arr, 1) == -1.0  # -1 is NOT the int8 sentinel
    assert _kb_at_int8(arr, 2) is None  # -128 is the sentinel
    assert _kb_at_int8(arr, 3) == 0.0
    assert _kb_at_int8(arr, -1) is None
    assert _kb_at_int8(arr, 99) is None


def test_kb_at_int64_back_compat() -> None:
    arr = np.array([5, -1, 0, 12], dtype=np.int64)
    assert _kb_at_int64(arr, 0) == 5.0
    assert _kb_at_int64(arr, 1) is None  # -1 → "no key bar yet"
    assert _kb_at_int64(arr, 2) == 0.0   # 0 is a valid "0 bars since"
    assert _kb_at_int64(arr, 3) == 12.0
    assert _kb_at_int64(arr, -1) is None
    assert _kb_at_int64(arr, 99) is None
