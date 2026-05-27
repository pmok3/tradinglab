"""Unit tests for :class:`tradinglab.core.lru_dict.LRUDict`.

Pins the capacity-eviction + LRU-touch contract shared by the
strategy-tester warmup cache and ``app._events_cache``.
"""

from __future__ import annotations

import pytest

from tradinglab.core.lru_dict import LRUDict


class TestCapacity:
    def test_set_within_capacity_keeps_all(self):
        c: LRUDict[str, int] = LRUDict(maxsize=3)
        c["a"] = 1
        c["b"] = 2
        c["c"] = 3
        assert len(c) == 3
        assert list(c.keys()) == ["a", "b", "c"]

    def test_set_over_capacity_evicts_oldest(self):
        c: LRUDict[str, int] = LRUDict(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        c["c"] = 3
        assert len(c) == 2
        assert "a" not in c
        assert list(c.keys()) == ["b", "c"]

    def test_maxsize_one_keeps_only_latest(self):
        c: LRUDict[str, int] = LRUDict(maxsize=1)
        c["a"] = 1
        c["b"] = 2
        c["c"] = 3
        assert list(c.keys()) == ["c"]

    def test_invalid_maxsize_raises(self):
        with pytest.raises(ValueError):
            LRUDict(maxsize=0)
        with pytest.raises(ValueError):
            LRUDict(maxsize=-1)

    def test_maxsize_property_readable(self):
        c: LRUDict[str, int] = LRUDict(maxsize=5)
        assert c.maxsize == 5


class TestLRUTouch:
    def test_get_refreshes_recency_so_touched_key_survives(self):
        c: LRUDict[str, int] = LRUDict(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        # Touch "a" so "b" becomes the LRU victim on the next insert.
        assert c.get("a") == 1
        c["c"] = 3
        assert "a" in c
        assert "b" not in c
        assert list(c.keys()) == ["a", "c"]

    def test_get_miss_returns_default_without_insertion(self):
        c: LRUDict[str, int] = LRUDict(maxsize=2)
        c["a"] = 1
        assert c.get("missing") is None
        assert c.get("missing", 42) == 42
        # Crucially: no phantom insert (would inflate len).
        assert len(c) == 1
        assert "missing" not in c

    def test_setitem_on_existing_key_refreshes_recency(self):
        c: LRUDict[str, int] = LRUDict(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        # Re-set "a" — it should move to the MRU end so "b" is the
        # next eviction victim.
        c["a"] = 99
        c["c"] = 3
        assert "a" in c
        assert "b" not in c
        assert c["a"] == 99

    def test_membership_does_not_touch(self):
        c: LRUDict[str, int] = LRUDict(maxsize=2)
        c["a"] = 1
        c["b"] = 2
        # ``in`` is plain OrderedDict semantics — does NOT touch.
        assert "a" in c
        c["c"] = 3
        # "a" was NOT touched by the ``in`` check, so it gets evicted.
        assert "a" not in c
        assert list(c.keys()) == ["b", "c"]


class TestDictABI:
    def test_clear_empties_and_preserves_maxsize(self):
        c: LRUDict[str, int] = LRUDict(maxsize=3)
        c["a"] = 1
        c["b"] = 2
        c.clear()
        assert len(c) == 0
        assert c.maxsize == 3
        # Still bounded after clear.
        c["x"] = 1
        c["y"] = 2
        c["z"] = 3
        c["w"] = 4
        assert len(c) == 3

    def test_pop_works_normally(self):
        c: LRUDict[str, int] = LRUDict(maxsize=3)
        c["a"] = 1
        c["b"] = 2
        assert c.pop("a") == 1
        assert "a" not in c
        assert c.pop("missing", 99) == 99

    def test_delitem(self):
        c: LRUDict[str, int] = LRUDict(maxsize=3)
        c["a"] = 1
        c["b"] = 2
        del c["a"]
        assert "a" not in c
        assert len(c) == 1

    def test_iteration_in_insertion_order(self):
        c: LRUDict[str, int] = LRUDict(maxsize=5)
        for k, v in [("a", 1), ("b", 2), ("c", 3)]:
            c[k] = v
        assert list(c) == ["a", "b", "c"]
        assert list(c.keys()) == ["a", "b", "c"]
        assert list(c.values()) == [1, 2, 3]
        assert list(c.items()) == [("a", 1), ("b", 2), ("c", 3)]

    def test_eviction_order_after_mixed_get_set(self):
        c: LRUDict[str, int] = LRUDict(maxsize=3)
        c["a"] = 1
        c["b"] = 2
        c["c"] = 3
        c.get("a")          # MRU = a
        c.get("b")          # MRU = b
        c["d"] = 4          # evicts "c"
        assert "c" not in c
        assert list(c.keys()) == ["a", "b", "d"]
