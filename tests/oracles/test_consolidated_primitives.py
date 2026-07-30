"""Oracles defending the consolidated primitives introduced by main's DRY sprint.

§7.34 records that seven primitives had each been copy-pasted across
subsystems and that several copies had **already drifted in production** —
two incompatible on-disk ID formats, an expectancy formula re-derived three
times, an EOD flatten that took the bar *open* at one site and the *close* at
the other. Those copies are now single-sourced.

Consolidation removes today's drift; it does not prevent tomorrow's. These
oracles assert the *laws* the single definitions must satisfy, so a future
"tidy-up" that re-normalises an ID format or rewrites a reduction is caught
by machinery rather than by whoever happens to review it.

Each test below names the specific failure mode §7.34 says the primitive
already caused.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.oracle


# --------------------------------------------------------------------------
# core/ids.py — two on-disk formats, both load-bearing
# --------------------------------------------------------------------------
#
# §7.34: entries / exits / strategy_tester persist dash-less 32-char hex;
# scanner / positions persist dashed 36-char UUIDs. Normalising them to one
# spelling would orphan every existing saved file. The danger is that this is
# invisible — both are "a UUID" to a casual reader.

_HEX_RE = re.compile(r"\A[0-9a-f]{32}\Z")
_DASHED_RE = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def test_id_formats_are_distinct_and_stable():
    from tradinglab.core.ids import new_id_dashed, new_id_hex

    h, d = new_id_hex(), new_id_dashed()
    assert _HEX_RE.match(h), f"new_id_hex must be 32 dash-less hex chars, got {h!r}"
    assert _DASHED_RE.match(d), f"new_id_dashed must be a dashed UUID, got {d!r}"
    assert len(h) == 32 and "-" not in h
    assert len(d) == 36 and d.count("-") == 4


def test_ids_are_unique_across_many_mints():
    from tradinglab.core.ids import new_id_dashed, new_id_hex

    assert len({new_id_hex() for _ in range(5_000)}) == 5_000
    assert len({new_id_dashed() for _ in range(5_000)}) == 5_000


@pytest.mark.parametrize("module_path,expected", [
    ("tradinglab.entries.model", "hex"),
    ("tradinglab.exits.model", "hex"),
    ("tradinglab.scanner.model", "dashed"),
])
def test_subsystems_keep_their_documented_on_disk_id_format(module_path, expected):
    """A subsystem silently switching format would orphan saved records.

    Each module keeps a ``_new_id`` alias (also a documented monkeypatch
    seam); this asserts the alias still yields the format that subsystem's
    existing files are written in.
    """
    import importlib

    mod = importlib.import_module(module_path)
    mint = getattr(mod, "_new_id", None)
    if mint is None:
        pytest.skip(f"{module_path} exposes no _new_id alias")
    value = mint()
    if expected == "hex":
        assert _HEX_RE.match(value), (
            f"{module_path}._new_id switched to a dashed ID — every saved "
            f"record in that subsystem uses 32-char hex; changing the format "
            f"orphans them. Got {value!r}")
    else:
        assert _DASHED_RE.match(value), (
            f"{module_path}._new_id switched to dash-less hex — every saved "
            f"record in that subsystem uses a dashed UUID. Got {value!r}")


def test_saved_record_id_survives_a_store_round_trip(tmp_path):
    """The ID written to disk must be the ID read back, byte for byte."""
    from tradinglab.entries import storage as est

    strategy = _entry_strategy("id-round-trip")
    original = strategy.id
    est.save(strategy, root=tmp_path)
    loaded = est.load(original, root=tmp_path)
    assert loaded is not None, "record not found under its own ID"
    assert loaded.id == original
    assert _HEX_RE.match(loaded.id), "entries IDs must stay 32-char hex on disk"


def _entry_strategy(name: str):
    from tradinglab.entries.model import (
        EntryStrategy,
        EntryTrigger,
        SizingKind,
        SizingRule,
        Universe,
    )
    return EntryStrategy(
        name=name,
        universe=Universe(symbols=("AAPL",)),
        trigger=EntryTrigger(price=101.0),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100.0),
    )


# --------------------------------------------------------------------------
# scanner/model.py — one tree visitor replacing seven hand-rolled walkers
# --------------------------------------------------------------------------
#
# §7.34: seven walkers were retired into `iter_nodes` / `iter_conditions` /
# `iter_field_refs` / `iter_tree_field_refs`. With seven copies a divergence
# was at least *visible*; with one visitor and several derived helpers, a
# composition that stops agreeing is silent. These are the composition laws.


def _tree():
    """A nested Group/Condition tree with mixed operand kinds.

    Deliberately includes a cross-symbol pin, a FieldRef-valued param and a
    scalar param, so both branches of ``iter_field_refs`` (LHS ref, and the
    FieldRef-valued subset of ``params``) are exercised and the scalar param
    is correctly skipped.
    """
    from tradinglab.scanner.model import (
        OP_GT,
        OP_NEW_HIGH_N,
        OP_WITHIN_PCT,
        Condition,
        FieldRef,
        Group,
    )

    left = FieldRef.indicator("ema", params={"length": 9})
    pinned = FieldRef.indicator("ema", params={"length": 21}, symbol="SPY")

    inner = Group(children=[
        # Two FieldRefs: the LHS and the `right` param.
        Condition(left=left, op=OP_GT,
                  params={"right": FieldRef.literal(5.0)}),
        # Mixed params: `target` is a FieldRef, `tolerance_pct` is a scalar
        # that iter_field_refs must SKIP.
        Condition(left=pinned, op=OP_WITHIN_PCT,
                  params={"target": FieldRef.literal(100.0),
                          "tolerance_pct": 2.5}),
    ])
    return Group(children=[
        inner,
        # Scalar-only params — contributes its LHS ref and nothing else.
        Condition(left=FieldRef.literal(1.0), op=OP_NEW_HIGH_N,
                  params={"n": 20}),
    ])


def test_iter_field_refs_skips_scalar_params():
    """The visitor must yield FieldRef-valued params only, never scalars."""
    from tradinglab.scanner.model import FieldRef, iter_tree_field_refs

    refs = list(iter_tree_field_refs(_tree()))
    assert refs, "fixture yielded no FieldRefs"
    assert all(isinstance(r, FieldRef) for r in refs), (
        "a scalar param leaked through iter_field_refs")
    # The fixture carries two scalar params (tolerance_pct, n); exactly the
    # five FieldRef operands should come back.
    assert len(refs) == 5, f"expected 5 FieldRefs, got {len(refs)}"
    # The pinned SPY ref must survive — cross-symbol pins are the subtlest
    # operand kind and the easiest for a rewritten walker to drop.
    assert any(getattr(r, "symbol", "") == "SPY" for r in refs), (
        "the cross-symbol pinned ref was lost by the tree walk")


def test_iter_conditions_is_exactly_the_condition_subset_of_iter_nodes():
    from tradinglab.scanner.model import Condition, iter_conditions, iter_nodes

    root = _tree()
    from_nodes = [n for n in iter_nodes(root) if isinstance(n, Condition)]
    from_conditions = list(iter_conditions(root))
    assert from_conditions == from_nodes, (
        "iter_conditions diverged from the Condition subset of iter_nodes — "
        "the derived helper no longer agrees with the base visitor")


def test_iter_tree_field_refs_is_the_documented_composition():
    """``iter_tree_field_refs`` is defined as the composition of the other two."""
    from tradinglab.scanner.model import (
        iter_conditions,
        iter_field_refs,
        iter_tree_field_refs,
    )

    root = _tree()
    composed = [r for c in iter_conditions(root) for r in iter_field_refs(c)]
    direct = list(iter_tree_field_refs(root))
    assert direct == composed, (
        "iter_tree_field_refs is no longer the composition of "
        "iter_conditions + iter_field_refs")


def test_visitors_accept_none_without_a_guard():
    """§7.34 states callers pass a nullable tree straight through."""
    from tradinglab.scanner.model import (
        iter_conditions,
        iter_nodes,
        iter_tree_field_refs,
    )

    assert list(iter_nodes(None)) == []
    assert list(iter_conditions(None)) == []
    assert list(iter_tree_field_refs(None)) == []


def test_iter_nodes_is_pre_order_parents_before_children():
    from tradinglab.scanner.model import Group, iter_nodes

    root = _tree()
    nodes = list(iter_nodes(root))
    assert nodes[0] is root, "pre-order must yield the root first"
    for i, node in enumerate(nodes):
        if isinstance(node, Group):
            for child in node.children:
                assert nodes.index(child) > i, "child yielded before its parent"


def test_tree_visitor_is_not_vacuous():
    """Anti-vacuity: the fixture tree must actually contain nested structure."""
    from tradinglab.scanner.model import (
        Condition,
        Group,
        iter_conditions,
        iter_nodes,
        iter_tree_field_refs,
    )

    root = _tree()
    nodes = list(iter_nodes(root))
    assert sum(1 for n in nodes if isinstance(n, Group)) >= 2, "needs nesting"
    assert sum(1 for n in nodes if isinstance(n, Condition)) >= 3
    assert len(list(iter_conditions(root))) >= 3
    assert len(list(iter_tree_field_refs(root))) >= 4, (
        "fixture must include FieldRef-valued params, not just LHS refs")


# --------------------------------------------------------------------------
# backtest/performance.py — one win/loss/expectancy reduction
# --------------------------------------------------------------------------
#
# §7.34: this reduction was inlined byte-identically in two builders and the
# expectancy formula independently re-derived a third time in
# strategy_tester/report.py. §7.34 also records the deliberate breakeven
# policy: pnl == 0 counts toward `count` but toward NEITHER wins nor losses,
# so win_rate + loss_rate <= 1.0. That policy is easy to "fix" by accident.


class _FakePost:
    def __init__(self, pnl):
        self.pnl = pnl


class _FakeRow:
    """Minimal TradeRow stand-in — the reduction only reads pnl/is_win/is_loss."""

    def __init__(self, pnl: float):
        self.post = _FakePost(pnl)

    @property
    def is_win(self) -> bool:
        return self.post.pnl > 0

    @property
    def is_loss(self) -> bool:
        return self.post.pnl < 0


def test_breakeven_trades_count_but_are_neither_win_nor_loss():
    from tradinglab.backtest.performance import summarize_trade_rows

    rows = [_FakeRow(10.0), _FakeRow(-5.0), _FakeRow(0.0)]
    s = summarize_trade_rows(rows)
    assert s.count == 3
    assert s.wins == 1 and s.losses == 1
    assert s.win_rate + s.loss_rate < 1.0, (
        "breakevens were folded into wins or losses — §7.34 documents that "
        "win_rate + loss_rate <= 1.0 is deliberate, pre-existing behaviour")


def test_totals_are_conserved_and_order_invariant():
    """Summing P&L is order-invariant; the reduction must be too."""
    from tradinglab.backtest.performance import summarize_trade_rows

    pnls = [12.5, -3.0, 0.0, 7.25, -11.75, 4.0]
    forward = summarize_trade_rows([_FakeRow(p) for p in pnls])
    reverse = summarize_trade_rows([_FakeRow(p) for p in reversed(pnls)])
    assert forward.total_pnl == pytest.approx(sum(pnls))
    assert forward.count == reverse.count
    assert forward.wins == reverse.wins and forward.losses == reverse.losses
    assert forward.total_pnl == pytest.approx(reverse.total_pnl)
    assert forward.expectancy == pytest.approx(reverse.expectancy)


def test_expectancy_matches_its_definition():
    """Pin the formula that §7.34 says was independently re-derived elsewhere."""
    from tradinglab.backtest.performance import summarize_trade_rows

    rows = [_FakeRow(10.0), _FakeRow(20.0), _FakeRow(-5.0), _FakeRow(-15.0)]
    s = summarize_trade_rows(rows)
    expected = s.win_rate * s.avg_win + s.loss_rate * s.avg_loss
    assert s.expectancy == pytest.approx(expected), (
        f"expectancy {s.expectancy} != win_rate*avg_win + loss_rate*avg_loss "
        f"({expected}) — the single definition drifted from its own parts")


def test_empty_bucket_is_all_zero_not_a_division_error():
    from tradinglab.backtest.performance import summarize_trade_rows

    s = summarize_trade_rows([])
    assert s.count == 0
    for field in ("wins", "losses", "win_rate", "loss_rate", "avg_pnl",
                  "total_pnl", "avg_win", "avg_loss", "expectancy"):
        assert getattr(s, field) == 0, f"{field} should be 0 on an empty bucket"


# --------------------------------------------------------------------------
# rendering.safe_remove_all — batch form of safe_remove
# --------------------------------------------------------------------------


def test_safe_remove_all_tolerates_already_detached_artists():
    """The batch form must swallow the same failures the scalar form does."""
    from tradinglab.rendering import safe_remove_all

    class _Artist:
        def __init__(self, boom=False):
            self.removed = False
            self._boom = boom

        def remove(self):
            if self._boom:
                raise ValueError("already detached")
            self.removed = True

    good_a, bad, good_b = _Artist(), _Artist(boom=True), _Artist()
    safe_remove_all([good_a, bad, good_b])
    assert good_a.removed and good_b.removed, (
        "one raising artist must not abort the batch — that is the whole "
        "point of the safe_ prefix")
    safe_remove_all([])
    safe_remove_all(None)
