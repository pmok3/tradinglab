"""Warmup walker cross-symbol grouping tests.

Pins :func:`required_warmup_bars_by_symbol` and the per-symbol
triple emission from :func:`_walk_field_kinds`.
"""

from __future__ import annotations

from tradinglab.entries.model import EntryStrategy, EntryTrigger
from tradinglab.entries.model import TriggerKind as EntryTriggerKind
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
)
from tradinglab.strategy_tester.warmup import (
    _walk_field_kinds,
    required_warmup_bars,
    required_warmup_bars_by_symbol,
)


def _entry_with_condition(cond_root: Group) -> EntryStrategy:
    return EntryStrategy(
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR,
            condition=cond_root,
        ),
    )


def test_walk_emits_active_symbol_as_empty_string():
    """A non-cross-symbol indicator ref yields ``("", kind, params)``."""
    cond = Group(combinator="and", children=[
        Condition(
            left=FieldRef.indicator("ema", params={"length": 8}),
            op=OP_GT,
            params={"right": FieldRef.literal(0.0)},
        ),
    ])
    triples = _walk_field_kinds(cond)
    assert triples == [("", "ema", {"length": 8})]


def test_walk_emits_pinned_symbol_for_cross_ticker():
    """A cross-symbol indicator ref yields ``("SPY", kind, params)``."""
    cond = Group(combinator="and", children=[
        Condition(
            left=FieldRef.indicator("ema", params={"length": 8}, symbol="SPY"),
            op=OP_GT,
            params={"right": FieldRef.literal(0.0)},
        ),
    ])
    triples = _walk_field_kinds(cond)
    assert triples == [("SPY", "ema", {"length": 8})]


def test_required_warmup_bars_by_symbol_active_only():
    cond = Group(combinator="and", children=[
        Condition(
            left=FieldRef.indicator("rsi", params={"length": 14}),
            op=OP_GT,
            params={"right": FieldRef.literal(50.0)},
        ),
    ])
    entry = _entry_with_condition(cond)
    by_sym = required_warmup_bars_by_symbol(entry, None)
    # Only the active symbol bucket is populated.
    assert set(by_sym.keys()) == {""}
    assert by_sym[""] > 0


def test_required_warmup_bars_by_symbol_mixes_active_and_cross():
    """Active RSI(14) + cross-symbol EMA(8) on SPY → two distinct buckets."""
    cond = Group(combinator="and", children=[
        Condition(
            left=FieldRef.indicator("rsi", params={"length": 14}),
            op=OP_GT,
            params={"right": FieldRef.literal(50.0)},
        ),
        Condition(
            left=FieldRef.indicator("ema", params={"length": 8}, symbol="SPY"),
            op=OP_GT,
            params={"right": FieldRef.literal(0.0)},
        ),
    ])
    entry = _entry_with_condition(cond)
    by_sym = required_warmup_bars_by_symbol(entry, None)
    assert set(by_sym.keys()) == {"", "SPY"}
    assert by_sym[""] > 0
    assert by_sym["SPY"] > 0


def test_required_warmup_bars_back_compat_unchanged():
    """Legacy ``required_warmup_bars`` keeps returning an int (Phase 3 deferred)."""
    cond = Group(combinator="and", children=[
        Condition(
            left=FieldRef.indicator("ema", params={"length": 8}, symbol="SPY"),
            op=OP_GT,
            params={"right": FieldRef.literal(0.0)},
        ),
    ])
    entry = _entry_with_condition(cond)
    n = required_warmup_bars(entry, None)
    assert isinstance(n, int)
    assert n > 0


def test_required_warmup_bars_by_symbol_empty_when_no_indicators():
    """No indicator triggers → empty dict (mirrors legacy ``return 0``)."""
    assert required_warmup_bars_by_symbol(None, None) == {}
