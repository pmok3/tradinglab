"""Unit tests for the prebuilt-arrays stash in ``data/normalize.py``.

Targets the identity-collision defense around
:func:`stash_arrays` / :func:`pop_prebuilt_arrays`, which exists to
prevent the AMD↔SPY y-axis aliasing bug documented in the production
docstring: Python reuses ``id()`` values after a list is GC'd, so a
naive ``{id → arrays}`` map would hand stale arrays to a different
list that happened to reuse the freed id. The fix is to store the
candle list reference alongside the arrays and verify identity on pop.

See also: ``src/tradinglab/data/normalize.spec.md``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import numpy as np
import pytest

from tradinglab.data import normalize as norm_mod
from tradinglab.data.normalize import (
    _PREBUILT_ARRAYS_MAX,
    CandleArrays,
    pop_prebuilt_arrays,
    stash_arrays,
)
from tradinglab.models import Candle


@pytest.fixture(autouse=True)
def _clear_prebuilt_arrays():
    """Reset module-level stash so test order doesn't matter."""
    norm_mod._PREBUILT_ARRAYS.clear()
    yield
    norm_mod._PREBUILT_ARRAYS.clear()


def _make_candles(n: int = 3) -> list[Candle]:
    """Build a small synthetic list of ``Candle`` objects."""
    base = datetime(2024, 3, 7, 14, 30, tzinfo=timezone.utc)
    return [
        Candle(
            date=base.replace(minute=30 + i),
            open=100.0 + i, high=101.0 + i,
            low=99.0 + i, close=100.5 + i,
            volume=1_000 + i, session="regular",
        )
        for i in range(n)
    ]


def _make_arrays(n: int = 3, *, seed: float = 0.0) -> CandleArrays:
    """Build a tiny ``CandleArrays`` of the requested length."""
    base = np.arange(n, dtype=np.float64) + seed
    return CandleArrays(
        opens=base.copy(),
        highs=base.copy() + 1.0,
        lows=base.copy() - 1.0,
        closes=base.copy() + 0.5,
        volumes=base.copy() * 100.0,
    )


def test_stash_and_pop_round_trip():
    candles = _make_candles(3)
    arrays = _make_arrays(3)

    stash_arrays(candles, arrays)
    assert id(candles) in norm_mod._PREBUILT_ARRAYS

    popped = pop_prebuilt_arrays(candles)

    assert popped is arrays
    assert id(candles) not in norm_mod._PREBUILT_ARRAYS
    assert len(norm_mod._PREBUILT_ARRAYS) == 0

    assert pop_prebuilt_arrays(candles) is None


def test_pop_rejects_id_collision_via_identity_check():
    """Regression test for the AMD/SPY id-collision bug.

    The stash retains a strong ref to the candle list so its ``id()``
    cannot be reused by another list while the stash holds it. Even if
    a stale entry somehow ended up at a colliding id slot, the
    ``stashed_candles is candles`` check defends against it.
    """
    candles_a = _make_candles(3)
    arrays_a = _make_arrays(3, seed=10.0)
    stash_arrays(candles_a, arrays_a)

    id_a = id(candles_a)
    assert id_a in norm_mod._PREBUILT_ARRAYS
    stashed_list_ref = norm_mod._PREBUILT_ARRAYS[id_a][0]
    assert stashed_list_ref is candles_a

    del candles_a
    assert id_a in norm_mod._PREBUILT_ARRAYS
    assert norm_mod._PREBUILT_ARRAYS[id_a][0] is stashed_list_ref

    candles_b = _make_candles(3)
    assert id(candles_b) != id(stashed_list_ref), (
        "While the stash retains a strong ref, the original id() cannot "
        "be reused by a freshly allocated list."
    )

    # Simulate the identity-collision scenario directly: force a stash
    # entry whose stored list does NOT match the lookup list. The
    # naive {id→arrays} implementation would happily return ``arrays_a``
    # for ``candles_b``; the production code's identity check must
    # return None instead.
    foreign_list = _make_candles(2)
    norm_mod._PREBUILT_ARRAYS[id(candles_b)] = (foreign_list, arrays_a)

    result = pop_prebuilt_arrays(candles_b)

    assert result is None
    assert id(candles_b) not in norm_mod._PREBUILT_ARRAYS


def test_stash_eviction_at_max_capacity():
    """Stashing more than ``_PREBUILT_ARRAYS_MAX`` evicts the oldest entries (FIFO)."""
    overflow = 5
    total = _PREBUILT_ARRAYS_MAX + overflow

    lists: list[list[Candle]] = []
    for i in range(total):
        lst = _make_candles(2)
        lists.append(lst)
        stash_arrays(lst, _make_arrays(2, seed=float(i)))

    assert len(norm_mod._PREBUILT_ARRAYS) == _PREBUILT_ARRAYS_MAX

    expected_remaining_ids = [id(lists[i]) for i in range(overflow, total)]
    assert list(norm_mod._PREBUILT_ARRAYS.keys()) == expected_remaining_ids

    for evicted in lists[:overflow]:
        assert id(evicted) not in norm_mod._PREBUILT_ARRAYS
        assert pop_prebuilt_arrays(evicted) is None

    for survivor in lists[overflow:]:
        assert id(survivor) in norm_mod._PREBUILT_ARRAYS
