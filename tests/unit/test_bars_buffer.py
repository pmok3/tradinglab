"""Tests for :class:`tradinglab.core.bars_buffer.BarsBuffer`.

Pure-logic tests; no Tk, no I/O. Verify the buffer's public contract:
append-then-view equivalence with ``Bars.from_candles``, in-place
``update_last`` semantics, capacity doubling, ``view(candles=)``
passthrough behaviour, and length validation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.core.bars_buffer import BarsBuffer
from tradinglab.models import Candle


# --- helpers ---------------------------------------------------------------


def _mk(i: int, *, session: str = "regular") -> Candle:
    base = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    return Candle(
        date=base + timedelta(minutes=i),
        open=100.0 + i,
        high=101.0 + i,
        low=99.0 + i,
        close=100.5 + i,
        volume=1_000_000.0 + 1000.0 * i,
        session=session,
    )


def _series(n: int) -> list[Candle]:
    return [_mk(i) for i in range(n)]


def _arrays_equal(a: Bars, b: Bars) -> None:
    assert a.open.tolist()       == b.open.tolist()
    assert a.high.tolist()       == b.high.tolist()
    assert a.low.tolist()        == b.low.tolist()
    assert a.close.tolist()      == b.close.tolist()
    assert a.volume.tolist()     == b.volume.tolist()
    assert a.timestamps.tolist() == b.timestamps.tolist()
    assert a.session.tolist()    == b.session.tolist()


# --- empty -----------------------------------------------------------------


def test_empty_buffer_view_is_empty_bars():
    buf = BarsBuffer()
    bars = buf.view()
    assert len(bars) == 0
    assert bars.open.size == 0
    assert bars.timestamps.dtype == np.dtype("datetime64[ns]")


# --- append ----------------------------------------------------------------


def test_append_then_view_matches_from_candles():
    candles = _series(7)
    buf = BarsBuffer()
    for c in candles:
        buf.append(c)
    assert len(buf) == 7
    _arrays_equal(buf.view(candles=candles), Bars.from_candles(candles))


def test_append_grows_capacity_smoothly():
    """Appending well past the initial capacity must not raise and must
    preserve every value."""
    n = 1000
    candles = _series(n)
    buf = BarsBuffer(initial_capacity=4)
    for c in candles:
        buf.append(c)
    assert len(buf) == n
    assert buf.capacity >= n
    _arrays_equal(buf.view(candles=candles), Bars.from_candles(candles))


# --- update_last -----------------------------------------------------------


def test_update_last_mutates_in_place():
    candles = _series(5)
    buf = BarsBuffer()
    for c in candles:
        buf.append(c)
    pre = buf.view(candles=candles)
    pre_close_5 = float(pre.close[-1])

    new_last = Candle(
        date=candles[-1].date,
        open=candles[-1].open,
        high=999.0,
        low=candles[-1].low,
        close=888.0,
        volume=candles[-1].volume,
        session=candles[-1].session,
    )
    buf.update_last(new_last)
    assert len(buf) == 5

    post_candles = candles[:-1] + [new_last]
    post = buf.view(candles=post_candles)
    assert float(post.high[-1])  == 999.0
    assert float(post.close[-1]) == 888.0
    # Length unchanged.
    assert post.close.size == 5
    # Sanity: the change really happened.
    assert float(post.close[-1]) != pre_close_5


def test_update_last_on_empty_raises():
    buf = BarsBuffer()
    with pytest.raises(IndexError):
        buf.update_last(_mk(0))


# --- extend / from_candles -------------------------------------------------


def test_from_candles_then_append_matches_full_rebuild():
    head = _series(20)
    tail = [_mk(i) for i in range(20, 25)]
    buf = BarsBuffer.from_candles(head)
    for c in tail:
        buf.append(c)
    full = head + tail
    _arrays_equal(buf.view(candles=full), Bars.from_candles(full))


def test_extend_bulk_grows_capacity_once():
    """``extend`` should not raise even when the buffer starts tiny."""
    buf = BarsBuffer(initial_capacity=1)
    candles = _series(50)
    buf.extend(candles)
    assert len(buf) == 50
    assert buf.capacity >= 50


# --- view kwargs -----------------------------------------------------------


def test_view_with_candles_attaches_back_reference():
    candles = _series(3)
    buf = BarsBuffer.from_candles(candles)
    bars = buf.view(candles=candles)
    assert bars.candles is candles or list(bars.candles) == list(candles)


def test_view_without_candles_has_no_back_reference():
    candles = _series(3)
    buf = BarsBuffer.from_candles(candles)
    bars = buf.view()
    assert bars.candles is None


def test_view_with_wrong_length_candles_raises():
    candles = _series(3)
    buf = BarsBuffer.from_candles(candles)
    with pytest.raises(ValueError):
        buf.view(candles=candles + [_mk(99)])


# --- clear -----------------------------------------------------------------


def test_clear_resets_length_preserves_capacity():
    buf = BarsBuffer(initial_capacity=4)
    for c in _series(20):
        buf.append(c)
    cap_before = buf.capacity
    assert cap_before >= 20

    buf.clear()
    assert len(buf) == 0
    assert buf.capacity == cap_before
    assert buf.view().open.size == 0

    # And we can keep using it.
    buf.append(_mk(0))
    assert len(buf) == 1


# --- view aliases buffer storage (not a copy) ------------------------------


def test_view_arrays_alias_buffer_until_capacity_grows():
    """As long as no append triggers re-allocation, the view aliases the
    buffer's internal arrays (zero-copy guarantee that delivers the
    perf win).
    """
    buf = BarsBuffer(initial_capacity=64)
    for c in _series(10):
        buf.append(c)
    bars = buf.view()
    # No copy — the view's `close` should share memory with the buffer slot.
    # We can't access _close directly without breaking encapsulation, so
    # we verify it's a view (np.may_share_memory) over a freshly produced
    # second view of the same buffer.
    bars_again = buf.view()
    assert np.may_share_memory(bars.close, bars_again.close)
