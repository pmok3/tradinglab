"""Unit tests for :mod:`tradinglab.strategy_tester.warmup`.

Covers the generalized warmup-detection flow:

* explicit ``instance.warmup_bars`` attribute fast path
* empirical first-finite detection fallback
* factory-miss / compute-failure → :data:`DEFAULT_WARMUP_BARS`
* per-process caching of ``(kind_id, params)`` lookups
* real-indicator end-to-end values for the common strategy-tester set
* strategy-tree walker (``required_warmup_bars``)
* bars → calendar-days conversion (``bars_to_calendar_days``)
"""

from __future__ import annotations

import numpy as np
import pytest

import tradinglab.indicators  # noqa: F401 — register built-in factories
from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import TriggerKind as EntryTriggerKind
from tradinglab.entries.model import Universe as EntryUniverse
from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
from tradinglab.exits.model import TriggerKind as ExitTriggerKind
from tradinglab.indicators.base import _BY_KIND_ID, register_indicator
from tradinglab.scanner.model import (
    OP_CROSSES_ABOVE,
    Condition,
    FieldRef,
    Group,
)
from tradinglab.strategy_tester import warmup as warmup_mod
from tradinglab.strategy_tester.warmup import (
    DEFAULT_WARMUP_BARS,
    WARMUP_SAFETY_MULTIPLIER,
    bars_to_calendar_days,
    required_warmup_bars,
    warmup_bars_for_kind,
)


@pytest.fixture(autouse=True)
def _clear_warmup_cache():
    """Isolate tests — empirical lookups are memoised per-process."""
    warmup_mod._WARMUP_CACHE.clear()
    yield
    warmup_mod._WARMUP_CACHE.clear()


# ---------------------------------------------------------------------------
# Real-indicator end-to-end values (hits both explicit + empirical paths)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,params,expected",
    [
        # Empirical path — first-finite index suffices.
        ("ema",       {"length": 8},                                         8),
        ("ema",       {"length": 21},                                       21),
        ("sma",       {"length": 50},                                       50),
        ("bbands",    {"length": 20},                                       20),
        ("vwap",      {},                                                    1),
        # Explicit warmup_bars opt-in path (Wilder convergence).
        ("rsi",       {"length": 14},                                       56),
        ("rsi",       {"length": 7},                                        28),
        ("atr",       {"length": 14},                                       56),
        ("adx",       {"length": 14},                                       56),
        # MACD uses its own canonical param names; explicit opt-in.
        ("macd",      {"fast_length": 12, "slow_length": 26, "signal_length": 9},  35),
        ("macd",      {"fast_length": 5,  "slow_length": 35, "signal_length": 5},  40),
        # ChandelierStops explicit opt-in (RMA-ATR chained with HH window).
        ("chandelier", {"lookback": 22,  "atr_period": 22},                 88),
        ("chandelier", {"lookback": 100, "atr_period": 5},                 100),
        # Unknown kind_ids fall back.
        ("unknown_thing",    {},                              DEFAULT_WARMUP_BARS),
        ("totally-made-up",  {"anything": 1},                 DEFAULT_WARMUP_BARS),
    ],
)
def test_warmup_for_real_indicators_match_expectations(
    kind: str, params: dict, expected: int,
) -> None:
    assert warmup_bars_for_kind(kind, params) == expected


def test_warmup_unknown_indicator_returns_default() -> None:
    assert warmup_bars_for_kind("does_not_exist", {}) == DEFAULT_WARMUP_BARS
    assert warmup_bars_for_kind("does_not_exist", None) == DEFAULT_WARMUP_BARS


def test_warmup_is_case_insensitive() -> None:
    assert warmup_bars_for_kind("EMA", {"length": 8}) == 8
    assert warmup_bars_for_kind("RSI", {"length": 14}) == 56


def test_warmup_garbage_params_fall_back_to_default() -> None:
    # Non-int length string → factory __init__ raises → safe fallback.
    assert warmup_bars_for_kind("ema", {"length": "abc"}) == DEFAULT_WARMUP_BARS


# ---------------------------------------------------------------------------
# Resolution-order primitives
# ---------------------------------------------------------------------------


def test_warmup_uses_explicit_attribute_when_present() -> None:
    """A factory whose instance exposes ``warmup_bars=42`` short-circuits
    empirical detection."""

    class _Fixed42:
        kind_id = "fake_fixed_42"
        warmup_bars = 42

        def compute_arr(self, _bars):  # pragma: no cover — must NOT run
            raise AssertionError("empirical path should not be taken")

    register_indicator("Fake Fixed 42", _Fixed42)
    try:
        assert warmup_bars_for_kind("fake_fixed_42", {}) == 42
    finally:
        _BY_KIND_ID.pop("fake_fixed_42", None)


def test_warmup_attribute_can_be_a_method() -> None:
    """``warmup_bars`` defined as a no-arg method (e.g. derived from
    instance params) also wins over empirical."""

    class _Method:
        kind_id = "fake_method_warmup"

        def __init__(self, length: int = 7) -> None:
            self.length = length

        def warmup_bars(self) -> int:
            return 3 * self.length

        def compute_arr(self, _bars):  # pragma: no cover
            raise AssertionError("empirical path should not be taken")

    register_indicator("Fake Method Warmup", _Method)
    try:
        assert warmup_bars_for_kind("fake_method_warmup", {"length": 10}) == 30
    finally:
        _BY_KIND_ID.pop("fake_method_warmup", None)


def test_warmup_falls_back_to_empirical_when_no_attribute() -> None:
    """Indicator with NO ``warmup_bars`` attribute → empirical first-finite
    index + 1 (here: 7 NaN-padded bars → first valid at 7 → warmup 8)."""

    class _Padded:
        kind_id = "fake_padded_7"
        overlay = True
        name = "Padded(7)"

        def compute_arr(self, bars):
            out = np.full(bars.close.size, np.nan, dtype=np.float64)
            if bars.close.size > 7:
                out[7:] = bars.close[7:]
            return {"v": out}

    register_indicator("Fake Padded 7", _Padded)
    try:
        # First finite index = 7 → warmup count = 7 + 1 = 8.
        assert warmup_bars_for_kind("fake_padded_7", {}) == 8
    finally:
        _BY_KIND_ID.pop("fake_padded_7", None)


def test_warmup_caches_repeated_lookups() -> None:
    """Second call with identical (kind_id, params) does NOT re-instantiate."""
    call_count = {"n": 0}

    class _Counted:
        kind_id = "fake_counted"
        warmup_bars = 5

        def __init__(self, length: int = 5) -> None:
            call_count["n"] += 1
            self.length = length

        def compute_arr(self, _bars):
            return {}

    register_indicator("Fake Counted", _Counted)
    try:
        warmup_bars_for_kind("fake_counted", {"length": 5})
        warmup_bars_for_kind("fake_counted", {"length": 5})
        warmup_bars_for_kind("fake_counted", {"length": 5})
        assert call_count["n"] == 1, f"expected single instantiation, got {call_count['n']}"
        # Different params → cache miss → second instantiation.
        warmup_bars_for_kind("fake_counted", {"length": 9})
        assert call_count["n"] == 2
    finally:
        _BY_KIND_ID.pop("fake_counted", None)


def test_warmup_empirical_all_nan_returns_default() -> None:
    """A broken/mis-parameterised indicator that returns all-NaN outputs
    over 500 bars should NOT report a misleading 0 — it gets the default."""

    class _AllNaN:
        kind_id = "fake_all_nan"

        def compute_arr(self, bars):
            return {"v": np.full(bars.close.size, np.nan, dtype=np.float64)}

    register_indicator("Fake All NaN", _AllNaN)
    try:
        assert warmup_bars_for_kind("fake_all_nan", {}) == DEFAULT_WARMUP_BARS
    finally:
        _BY_KIND_ID.pop("fake_all_nan", None)


# ---------------------------------------------------------------------------
# Helpers for required_warmup_bars
# ---------------------------------------------------------------------------


def _indicator_ref(kind_id: str, length: int) -> FieldRef:
    return FieldRef(kind="indicator", id=kind_id, params={"length": length})


def _literal(value: float) -> FieldRef:
    return FieldRef(kind="literal", value=value)


def _builtin(name: str) -> FieldRef:
    return FieldRef(kind="builtin", id=name)


def _cross_condition(left_ema: int, right_ema: int) -> Condition:
    """EMA(left) crosses_above EMA(right)."""
    return Condition(
        left=_indicator_ref("ema", left_ema),
        op=OP_CROSSES_ABOVE,
        params={
            "right": _indicator_ref("ema", right_ema),
            "lookback": _literal(1),
        },
    )


def _entry_with_condition(cond: Condition) -> EntryStrategy:
    grp = Group(combinator="and", children=[cond])
    return EntryStrategy(
        id="e1", name="t",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR,
            condition=grp,
            interval="5m",
        ),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
    )


def _market_entry() -> EntryStrategy:
    return EntryStrategy(
        id="e1", name="m",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
    )


def _stop_exit() -> ExitStrategy:
    return ExitStrategy(
        id="x1", name="stop",
        legs=[ExitLeg(id="leg1", triggers=[
            ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=99.0, qty_pct=100.0),
        ])],
    )


# ---------------------------------------------------------------------------
# required_warmup_bars
# ---------------------------------------------------------------------------


def test_required_warmup_bars_market_entry_stop_exit_is_zero() -> None:
    """No indicator-style trigger anywhere → no warmup needed."""
    assert required_warmup_bars(_market_entry(), _stop_exit()) == 0


def test_required_warmup_bars_handles_none_strategies() -> None:
    assert required_warmup_bars(None, None) == 0
    assert required_warmup_bars(_market_entry(), None) == 0
    assert required_warmup_bars(None, _stop_exit()) == 0


def test_required_warmup_bars_ema_cross_picks_max_with_safety_margin() -> None:
    """EMA(3) crosses EMA(8): max(3,8)=8 × 1.5 = 12."""
    entry = _entry_with_condition(_cross_condition(3, 8))
    assert required_warmup_bars(entry, _stop_exit()) == 12


def test_required_warmup_bars_rsi_dominates_over_vwap() -> None:
    """An AND of RSI(14) and a VWAP comparison — RSI(56) wins.

    56 × 1.5 = 84.
    """
    cond_rsi = Condition(
        left=_indicator_ref("rsi", 14),
        op=OP_CROSSES_ABOVE,
        params={"right": _literal(50.0), "lookback": _literal(1)},
    )
    cond_vwap = Condition(
        left=_builtin("close"),
        op=OP_CROSSES_ABOVE,
        params={
            "right": FieldRef(kind="indicator", id="vwap", params={}),
            "lookback": _literal(1),
        },
    )
    grp = Group(combinator="and", children=[cond_rsi, cond_vwap])
    entry = EntryStrategy(
        id="e1", name="t",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR, condition=grp, interval="5m"
        ),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
    )
    assert required_warmup_bars(entry, _stop_exit()) == 84


def test_required_warmup_bars_nested_group() -> None:
    """Deeply-nested group (Group of Groups) still walks every indicator."""
    inner = Group(combinator="or", children=[_cross_condition(3, 8)])
    outer = Group(combinator="and", children=[inner, _cross_condition(5, 21)])
    entry = EntryStrategy(
        id="e1", name="t",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR, condition=outer, interval="5m"
        ),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
    )
    # max(3, 8, 5, 21) = 21; 21 * 1.5 = 31.5 → ceil = 32
    assert required_warmup_bars(entry, _stop_exit()) == 32


def test_required_warmup_bars_chandelier_exit() -> None:
    """CHANDELIER exit trigger reads its colocated lookback / atr_period."""
    exit_strat = ExitStrategy(
        id="x1", name="chand",
        legs=[ExitLeg(id="leg1", triggers=[
            ExitTrigger(
                kind=ExitTriggerKind.CHANDELIER,
                chandelier_lookback=22,
                chandelier_atr_period=22,
                chandelier_multiplier=3.0,
                qty_pct=100.0,
            ),
        ])],
    )
    # max(22, 4*22=88) = 88; 88 * 1.5 = 132
    assert required_warmup_bars(_market_entry(), exit_strat) == 132


def test_required_warmup_bars_exit_indicator_condition() -> None:
    """INDICATOR exit triggers' condition trees are walked too."""
    cond = Condition(
        left=_indicator_ref("ema", 50),
        op=OP_CROSSES_ABOVE,
        params={"right": _literal(0.0), "lookback": _literal(1)},
    )
    grp = Group(combinator="and", children=[cond])
    exit_strat = ExitStrategy(
        id="x1", name="ind",
        legs=[ExitLeg(id="leg1", triggers=[
            ExitTrigger(
                kind=ExitTriggerKind.INDICATOR,
                condition=grp,
                interval="5m",
                qty_pct=100.0,
            ),
        ])],
    )
    # 50 * 1.5 = 75
    assert required_warmup_bars(_market_entry(), exit_strat) == 75


def test_required_warmup_bars_disabled_leg_and_trigger_skipped() -> None:
    """Disabled legs/triggers don't contribute to the warmup math."""
    cond = Condition(
        left=_indicator_ref("ema", 50),
        op=OP_CROSSES_ABOVE,
        params={"right": _literal(0.0), "lookback": _literal(1)},
    )
    grp = Group(combinator="and", children=[cond])
    exit_strat = ExitStrategy(
        id="x1", name="ind",
        legs=[ExitLeg(id="leg1", enabled=False, triggers=[
            ExitTrigger(
                kind=ExitTriggerKind.INDICATOR,
                condition=grp, interval="5m", qty_pct=100.0,
            ),
        ])],
    )
    assert required_warmup_bars(_market_entry(), exit_strat) == 0


# ---------------------------------------------------------------------------
# bars_to_calendar_days
# ---------------------------------------------------------------------------


def test_bars_to_calendar_days_zero_returns_zero() -> None:
    assert bars_to_calendar_days(0, "5m") == 0
    assert bars_to_calendar_days(-5, "5m") == 0


def test_bars_to_calendar_days_5m_one_day() -> None:
    # 78 5m bars = 1 RTH day; × 1.5 = 2 calendar days.
    assert bars_to_calendar_days(78, "5m") == 2


def test_bars_to_calendar_days_1m_one_day() -> None:
    # 390 1m bars = 1 RTH day; × 1.5 = 2 calendar days.
    assert bars_to_calendar_days(390, "1m") == 2


def test_bars_to_calendar_days_daily_interval() -> None:
    # 8 daily bars × 1.5 = 12 calendar days.
    assert bars_to_calendar_days(8, "1d") == 12


def test_bars_to_calendar_days_15m() -> None:
    # 26 15m bars = 1 RTH day → 2 calendar days.
    assert bars_to_calendar_days(26, "15m") == 2
    # 52 15m bars = 2 RTH days → 3 calendar days.
    assert bars_to_calendar_days(52, "15m") == 3


def test_bars_to_calendar_days_unknown_interval_treated_as_daily() -> None:
    assert bars_to_calendar_days(4, "weird") == bars_to_calendar_days(4, "1d")


def test_bars_to_calendar_days_partial_day_rounds_up() -> None:
    # 1 5m bar still needs to fetch at least 1 calendar day.
    assert bars_to_calendar_days(1, "5m") == 2  # ceil(1/78)=1 → 1*1.5=1.5 → ceil = 2


def test_warmup_safety_multiplier_is_15() -> None:
    """Pin the safety multiplier — the spec.md / CLAUDE.md call it out."""
    assert WARMUP_SAFETY_MULTIPLIER == 1.5
