"""Tests for the shared Group/Condition traversal primitives.

`scanner.model` grew `iter_nodes` / `iter_conditions` / `iter_field_refs` /
`iter_tree_field_refs` to replace ~14 independently hand-rolled recursions
over the same tree (in `scanner/`, `strategy_tester/` and `gui/`). These
pin the traversal contract the collectors now depend on.
"""

from __future__ import annotations

from tradinglab.scanner.model import (
    Condition,
    FieldRef,
    Group,
    iter_conditions,
    iter_field_refs,
    iter_nodes,
    iter_tree_field_refs,
)


def _cond(cid: str, left_id: str = "close", *, op: str = ">", **params) -> Condition:
    if op == ">" and not params:
        params = {"right": _lit(1.0)}
    return Condition(
        id=cid,
        left=FieldRef(kind="builtin", id=left_id),
        op=op,
        params=params,
    )


def _lit(value: float) -> FieldRef:
    return FieldRef(kind="literal", value=value)


def _tree() -> Group:
    """A 3-level tree: root(and)[ c1, sub(or)[ c2, deep(and)[ c3 ] ] ]."""
    c1 = _cond("c1", "close", right=_lit(1.0))
    c2 = _cond("c2", "volume", right=2.0)
    c3 = _cond("c3", "high", right=FieldRef(kind="indicator", id="ema"))
    deep = Group(id="deep", combinator="and", children=[c3])
    sub = Group(id="sub", combinator="or", children=[c2, deep])
    return Group(id="root", combinator="and", children=[c1, sub])


class TestIterNodes:
    def test_none_yields_nothing(self) -> None:
        assert list(iter_nodes(None)) == []

    def test_bare_condition_yields_itself(self) -> None:
        c = _cond("solo")
        assert list(iter_nodes(c)) == [c]

    def test_preorder_parents_before_children(self) -> None:
        ids = [n.id for n in iter_nodes(_tree())]
        assert ids == ["root", "c1", "sub", "c2", "deep", "c3"]

    def test_empty_group_yields_only_itself(self) -> None:
        g = Group(id="empty", combinator="and", children=[])
        assert [n.id for n in iter_nodes(g)] == ["empty"]


class TestIterConditions:
    def test_yields_only_leaves_in_tree_order(self) -> None:
        assert [c.id for c in iter_conditions(_tree())] == ["c1", "c2", "c3"]

    def test_none_yields_nothing(self) -> None:
        assert list(iter_conditions(None)) == []

    def test_bare_condition(self) -> None:
        c = _cond("solo")
        assert list(iter_conditions(c)) == [c]

    def test_matches_legacy_all_conditions(self) -> None:
        """`ScanDefinition.all_conditions` delegates here; same result."""
        root = _tree()
        assert [c.id for c in iter_conditions(root)] == ["c1", "c2", "c3"]


class TestIterFieldRefs:
    def test_left_first_then_params(self) -> None:
        right = FieldRef(kind="indicator", id="ema")
        c = _cond("c", "close", right=right)
        refs = list(iter_field_refs(c))
        assert refs[0].id == "close"
        assert refs[1] is right

    def test_scalar_params_skipped(self) -> None:
        c = _cond("c", "volume", right=2.0)
        assert [r.id for r in iter_field_refs(c)] == ["volume"]

    def test_multiple_field_params(self) -> None:
        lo = _lit(1.0)
        hi = _lit(9.0)
        c = _cond("c", "close", op="between", low=lo, high=hi)
        assert list(iter_field_refs(c)) == [c.left, lo, hi]

    def test_none_left_is_skipped(self) -> None:
        """Ops like `inside_bar` carry no LHS."""
        c = Condition(id="c", left=None, op="inside_bar", params={})
        assert list(iter_field_refs(c)) == []


class TestIterTreeFieldRefs:
    def test_collects_across_whole_tree(self) -> None:
        ids = [r.id for r in iter_tree_field_refs(_tree())]
        # c1.left, c1.right(literal), c2.left, c3.left, c3.right(ema)
        assert ids == ["close", "", "volume", "high", "ema"]

    def test_none_yields_nothing(self) -> None:
        assert list(iter_tree_field_refs(None)) == []

    def test_equals_composition_of_the_two_primitives(self) -> None:
        root = _tree()
        composed = [r for c in iter_conditions(root) for r in iter_field_refs(c)]
        assert list(iter_tree_field_refs(root)) == composed


class TestConsumersDelegate:
    """The migrated collectors must agree with the shared traversal."""

    def test_evaluator_field_symbols_uses_shared_walk(self) -> None:
        from tradinglab.strategy_tester.evaluator import _walk_field_symbols

        pinned = FieldRef(kind="indicator", id="ema", symbol="spy")
        c = _cond("c", "close", right=pinned)
        root = Group(id="r", combinator="and", children=[c])
        assert _walk_field_symbols(root) == ["SPY"]

    def test_warmup_field_kinds_uses_shared_walk(self) -> None:
        from tradinglab.strategy_tester.warmup import _walk_field_kinds

        ref = FieldRef(kind="indicator", id="rsi", params={"length": 14})
        c = _cond("c", "close", right=ref)
        root = Group(id="r", combinator="and", children=[c])
        assert _walk_field_kinds(root) == [("", "rsi", {"length": 14})]

    def test_warmup_field_kinds_tolerates_none(self) -> None:
        from tradinglab.strategy_tester.warmup import _walk_field_kinds

        assert _walk_field_kinds(None) == []

    def test_screenshot_field_refs_uses_shared_walk(self) -> None:
        from tradinglab.strategy_tester.screenshot import _walk_field_refs

        assert _walk_field_refs(None) == []
        assert [r.id for r in _walk_field_refs(_tree())] == [
            "close", "", "volume", "high", "ema",
        ]

    def test_screenshot_field_refs_ignores_foreign_objects(self) -> None:
        from tradinglab.strategy_tester.screenshot import _walk_field_refs

        assert _walk_field_refs("not a node") == []
