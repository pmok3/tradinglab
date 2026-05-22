"""Tests for :class:`tradinglab.core.bars_registry.BarsRegistry`.

Covers ``get_view`` lifecycle, memo reuse / rebuild on fingerprint
change, invalidation semantics, stat counters, and per-symbol /
per-interval isolation. Pure-logic; no Tk, no real network.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pytest

import tradinglab.indicators  # noqa: F401  (registers indicators)
from tradinglab.core.bars_registry import BarsRegistry, BarsView
from tradinglab.data.multi_interval_cache import MultiIntervalCache
from tradinglab.models import Candle


# --- helpers ---------------------------------------------------------------


def _candles(n: int, *, start_close: float = 100.0,
             start: datetime = datetime(2026, 5, 4, 9, 30),
             interval_min: int = 5) -> List[Candle]:
    out = []
    for i in range(n):
        c = start_close + i
        out.append(Candle(
            date=start + timedelta(minutes=i * interval_min),
            open=c - 0.5, high=c + 1.0, low=c - 1.0, close=c,
            volume=1000 + i, session="regular",
        ))
    return out


def _make_registry() -> BarsRegistry:
    cache = MultiIntervalCache()  # no fetch_history → manual injection
    return BarsRegistry(cache)


# --- get_view --------------------------------------------------------------


def test_get_view_returns_none_when_cache_empty():
    reg = _make_registry()
    assert reg.get_view("AAPL", "5m") is None


def test_get_view_returns_view_after_set_bars():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    view = reg.get_view("AAPL", "5m")
    assert view is not None
    assert isinstance(view, BarsView)
    assert len(view.bars) == 5
    # Memo's candles list must align with the buffer length.
    assert len(view.memo.candles) == 5


def test_memo_reused_across_two_get_view_calls_no_rebuild():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    view1 = reg.get_view("AAPL", "5m")
    view2 = reg.get_view("AAPL", "5m")
    assert view1 is not None and view2 is not None
    # Same memo identity → reuse, not rebuild.
    assert view1.memo is view2.memo
    s = reg.stats()
    assert s["memos_reused"] == 1
    assert s["memos_rebuilt"] == 1
    assert s["views_built"] == 2


def test_memo_rebuilt_on_fingerprint_change():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    view1 = reg.get_view("AAPL", "5m")
    # Re-set with a longer candle list — fingerprint differs (length).
    reg._cache.set_bars("AAPL", "5m", _candles(6))
    view2 = reg.get_view("AAPL", "5m")
    assert view1 is not None and view2 is not None
    # Different memo identity since fingerprint changed.
    assert view1.memo is not view2.memo
    s = reg.stats()
    assert s["memos_rebuilt"] == 2
    assert s["memos_reused"] == 0


def test_invalidate_symbol_drops_all_intervals():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    reg._cache.set_bars("AAPL", "15m", _candles(3))
    v5_a = reg.get_view("AAPL", "5m")
    v15_a = reg.get_view("AAPL", "15m")
    assert v5_a is not None and v15_a is not None
    reg.invalidate("AAPL")
    # Both intervals dropped.
    v5_b = reg.get_view("AAPL", "5m")
    v15_b = reg.get_view("AAPL", "15m")
    assert v5_b is not None and v15_b is not None
    assert v5_b.memo is not v5_a.memo
    assert v15_b.memo is not v15_a.memo


def test_invalidate_symbol_interval_drops_only_that_pair():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    reg._cache.set_bars("AAPL", "15m", _candles(3))
    v5_a = reg.get_view("AAPL", "5m")
    v15_a = reg.get_view("AAPL", "15m")
    reg.invalidate("AAPL", "5m")
    v5_b = reg.get_view("AAPL", "5m")
    v15_b = reg.get_view("AAPL", "15m")
    # 5m memo rebuilt, 15m memo reused.
    assert v5_b.memo is not v5_a.memo
    assert v15_b.memo is v15_a.memo


def test_clear_empties_everything():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    reg._cache.set_bars("MSFT", "15m", _candles(3))
    reg.get_view("AAPL", "5m")
    reg.get_view("MSFT", "15m")
    reg.clear()
    # Re-getting after clear rebuilds memos.
    s_before = dict(reg.stats())
    reg.get_view("AAPL", "5m")
    reg.get_view("MSFT", "15m")
    s_after = reg.stats()
    assert s_after["memos_rebuilt"] == s_before["memos_rebuilt"] + 2


def test_stats_counters_increment_correctly():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    s0 = reg.stats()
    assert s0["views_built"] == 0
    assert s0["memos_reused"] == 0
    assert s0["memos_rebuilt"] == 0
    reg.get_view("AAPL", "5m")
    reg.get_view("AAPL", "5m")  # second call reuses
    reg.get_view("AAPL", "5m")  # third call reuses
    s1 = reg.stats()
    assert s1["views_built"] == 3
    assert s1["memos_rebuilt"] == 1
    assert s1["memos_reused"] == 2


def test_two_different_symbols_dont_share_memo():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    reg._cache.set_bars("MSFT", "5m", _candles(5))
    a = reg.get_view("AAPL", "5m")
    m = reg.get_view("MSFT", "5m")
    assert a is not None and m is not None
    assert a.memo is not m.memo


def test_two_different_intervals_same_symbol_dont_share_memo():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    reg._cache.set_bars("AAPL", "15m", _candles(5))
    v5 = reg.get_view("AAPL", "5m")
    v15 = reg.get_view("AAPL", "15m")
    assert v5 is not None and v15 is not None
    assert v5.memo is not v15.memo


def test_view_bars_length_matches_memo_candle_length():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(7))
    view = reg.get_view("AAPL", "5m")
    assert view is not None
    assert len(view.bars) == 7
    assert len(view.memo.candles) == 7
    # Buffer length matches as well.
    assert len(view.buffer) == 7


def test_re_get_after_invalidate_returns_fresh_memo():
    reg = _make_registry()
    reg._cache.set_bars("AAPL", "5m", _candles(5))
    view1 = reg.get_view("AAPL", "5m")
    reg.invalidate("AAPL", "5m")
    view2 = reg.get_view("AAPL", "5m")
    assert view1 is not None and view2 is not None
    assert view1.memo is not view2.memo
