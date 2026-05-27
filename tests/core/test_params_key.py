"""Unit tests for :func:`tradinglab.core.params_key.freeze_params`."""

from __future__ import annotations

from tradinglab.core.params_key import freeze_params


class TestEmptyAndNone:
    def test_none_collapses_to_empty_tuple(self):
        assert freeze_params(None) == ()

    def test_empty_dict_collapses_to_empty_tuple(self):
        assert freeze_params({}) == ()


class TestScalars:
    def test_single_int_pair(self):
        assert freeze_params({"length": 14}) == (("length", 14),)

    def test_string_value(self):
        assert freeze_params({"source": "close"}) == (("source", "close"),)

    def test_mixed_types(self):
        result = freeze_params({"length": 14, "source": "close", "off": False})
        # Sorted by key
        assert result == (("length", 14), ("off", False), ("source", "close"))


class TestDeterminism:
    def test_insertion_order_independent(self):
        a = freeze_params({"b": 2, "a": 1, "c": 3})
        b = freeze_params({"a": 1, "b": 2, "c": 3})
        c = freeze_params({"c": 3, "a": 1, "b": 2})
        assert a == b == c

    def test_two_equal_dicts_hash_to_same_key(self):
        k1 = freeze_params({"length": 14, "source": "close"})
        k2 = freeze_params({"source": "close", "length": 14})
        # Usable as a dict key
        d = {k1: "value"}
        assert d[k2] == "value"


class TestContainers:
    def test_list_value_becomes_tuple(self):
        result = freeze_params({"levels": [20, 50, 80]})
        assert result == (("levels", (20, 50, 80)),)
        # Result is hashable
        hash(result)

    def test_tuple_value_stays_tuple(self):
        result = freeze_params({"levels": (20, 50, 80)})
        assert result == (("levels", (20, 50, 80)),)

    def test_dict_value_becomes_sorted_tuple_of_pairs(self):
        result = freeze_params({"settings": {"b": 2, "a": 1}})
        assert result == (("settings", (("a", 1), ("b", 2))),)
        hash(result)

    def test_set_value_becomes_frozenset(self):
        result = freeze_params({"tags": {"a", "b"}})
        ((key, value),) = result
        assert key == "tags"
        assert value == frozenset({"a", "b"})

    def test_nested_containers_recursively_frozen(self):
        result = freeze_params({
            "rules": [{"op": "gt", "rhs": 0}, {"op": "lt", "rhs": 100}],
        })
        ((key, value),) = result
        assert key == "rules"
        # Each inner dict became a sorted-tuple-of-pairs
        assert value == (
            (("op", "gt"), ("rhs", 0)),
            (("op", "lt"), ("rhs", 100)),
        )
        # Whole thing is still hashable
        hash(result)


class TestKeyCoercion:
    def test_non_string_keys_coerced(self):
        result = freeze_params({1: "a", 2: "b"})
        assert result == (("1", "a"), ("2", "b"))
