"""Unit tests for the Strategy Tester intraday-interval compatibility guard.

Pins the contract that a strategy referencing an intraday-only indicator
(VWAP, RVOL cumulative / time-of-day, RRVOL, Prior Day H/L) is flagged as
incompatible with a daily / weekly / monthly run interval — the root cause
of the "20-bar new high breakout produces zero trades on 1d" bug, where
``close > vwap`` is NaN-unknown every daily bar so the breakout never fires.

Audit ``intraday-interval-guard``.
"""
from __future__ import annotations

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
from tradinglab.indicators.vwap import VWAP
from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group
from tradinglab.strategy_tester.interval_compat import (
    incompatible_arming_problems,
    incompatible_indicators_for_interval,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _indicator_entry(condition: Group, *, interval: str = "5m") -> EntryStrategy:
    return EntryStrategy(
        id="e",
        name="Indicator Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR,
            condition=condition,
            interval=interval,
        ),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=1.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _close_gt_indicator(kind_id: str, params: dict | None = None) -> Group:
    """``close > <indicator>`` one-leaf group."""
    return Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={
                    "right": FieldRef(
                        kind="indicator", id=kind_id, params=params or {},
                    ),
                },
                interval="5m",
            ),
        ],
    )


def _market_entry() -> EntryStrategy:
    return EntryStrategy(
        id="e",
        name="Market Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=1.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _stop_exit() -> ExitStrategy:
    return ExitStrategy(
        id="x", name="stop",
        legs=[ExitLeg(id="l", triggers=[ExitTrigger(
            kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0)])],
    )


def _vwap_exit() -> ExitStrategy:
    """Exit whose INDICATOR leg references VWAP (close < vwap)."""
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op="<",
                params={"right": FieldRef(kind="indicator", id="vwap")},
                interval="5m",
            ),
        ],
    )
    return ExitStrategy(
        id="x", name="vwap-exit",
        legs=[ExitLeg(id="l", triggers=[ExitTrigger(
            kind=ExitTriggerKind.INDICATOR, condition=cond, qty_pct=100.0)])],
    )


# ---------------------------------------------------------------------------
# VWAP availability (the root-cause fix — VWAP now declares intraday-only)
# ---------------------------------------------------------------------------


def test_vwap_is_unavailable_on_daily() -> None:
    avail = VWAP.is_available_for("1d")
    assert avail.ok is False
    assert "intraday" in avail.reason.lower()


def test_vwap_is_available_on_intraday() -> None:
    assert VWAP.is_available_for("5m").ok is True
    assert VWAP.is_available_for("1m").ok is True


# ---------------------------------------------------------------------------
# incompatible_indicators_for_interval
# ---------------------------------------------------------------------------


def test_vwap_entry_flagged_on_daily() -> None:
    entry = _indicator_entry(_close_gt_indicator("vwap"))
    flagged = incompatible_indicators_for_interval(entry, _stop_exit(), "1d")
    assert [name for name, _ in flagged] == ["VWAP"]
    assert "intraday" in flagged[0][1].lower()


def test_vwap_entry_clean_on_intraday() -> None:
    entry = _indicator_entry(_close_gt_indicator("vwap"))
    assert incompatible_indicators_for_interval(entry, _stop_exit(), "5m") == []


def test_market_entry_stop_exit_clean_on_daily() -> None:
    """No indicators referenced → nothing to flag, any interval is fine."""
    assert incompatible_indicators_for_interval(
        _market_entry(), _stop_exit(), "1d") == []


def test_pure_builtin_breakout_clean_on_daily() -> None:
    """A new-high breakout that drops the VWAP filter runs on daily."""
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="high"),
                op="new_high_n_bars",
                params={"n": 20},
                interval="5m",
            ),
        ],
    )
    entry = _indicator_entry(cond)
    assert incompatible_indicators_for_interval(entry, _stop_exit(), "1d") == []


def test_rvol_simple_mode_clean_on_daily() -> None:
    """RVOL ``simple`` mode works on every interval — must NOT be flagged."""
    entry = _indicator_entry(_close_gt_indicator("rvol", {"mode": "simple"}))
    assert incompatible_indicators_for_interval(entry, _stop_exit(), "1d") == []


def test_rvol_cumulative_mode_flagged_on_daily() -> None:
    """RVOL ``cumulative`` mode is intraday-only — must be flagged."""
    entry = _indicator_entry(
        _close_gt_indicator("rvol", {"mode": "cumulative"}))
    flagged = incompatible_indicators_for_interval(entry, _stop_exit(), "1d")
    assert flagged, "RVOL cumulative on 1d should be flagged"


def test_exit_side_vwap_flagged_on_daily() -> None:
    """An intraday-only indicator on the EXIT side is flagged too."""
    flagged = incompatible_indicators_for_interval(
        _market_entry(), _vwap_exit(), "1d")
    assert [name for name, _ in flagged] == ["VWAP"]


def test_blank_interval_returns_empty() -> None:
    entry = _indicator_entry(_close_gt_indicator("vwap"))
    assert incompatible_indicators_for_interval(entry, _stop_exit(), "") == []


def test_dedup_by_display_name() -> None:
    """Two VWAP references collapse to a single entry."""
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="indicator", id="vwap")},
                interval="5m",
            ),
            Condition(
                left=FieldRef(kind="builtin", id="open"),
                op=OP_GT,
                params={"right": FieldRef(kind="indicator", id="vwap")},
                interval="5m",
            ),
        ],
    )
    entry = _indicator_entry(cond)
    flagged = incompatible_indicators_for_interval(entry, _stop_exit(), "1d")
    assert len(flagged) == 1


def test_unknown_kind_id_fails_open() -> None:
    """An unknown indicator kind_id is treated as available (not flagged)."""
    entry = _indicator_entry(_close_gt_indicator("totally-not-an-indicator"))
    assert incompatible_indicators_for_interval(entry, _stop_exit(), "1d") == []


# ---------------------------------------------------------------------------
# incompatible_arming_problems (live + sandbox, per-condition interval)
# ---------------------------------------------------------------------------


def _close_gt_indicator_at(
    kind_id: str, cond_interval: str, params: dict | None = None,
) -> Group:
    return Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={
                    "right": FieldRef(
                        kind="indicator", id=kind_id, params=params or {},
                    ),
                },
                interval=cond_interval,
            ),
        ],
    )


def _builtin_breakout_at(cond_interval: str) -> Group:
    return Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="high"),
                op="new_high_n_bars",
                params={"n": 20},
                interval=cond_interval,
            ),
        ],
    )


def test_arming_vwap_5m_clean_live() -> None:
    """A 5m VWAP strategy is armable live — it works on 5m bars."""
    entry = _indicator_entry(_close_gt_indicator_at("vwap", "5m"), interval="5m")
    assert incompatible_arming_problems(entry) == []


def test_arming_vwap_5m_blocked_in_1d_sandbox() -> None:
    """A 5m strategy can't fire in a 1d-only sandbox (no 5m bars)."""
    entry = _indicator_entry(_close_gt_indicator_at("vwap", "5m"), interval="5m")
    problems = incompatible_arming_problems(
        entry, available_intervals=frozenset({"1d"}))
    assert problems
    assert any("5m" in p and "sandbox" in p for p in problems)


def test_arming_vwap_5m_clean_in_5m_sandbox() -> None:
    entry = _indicator_entry(_close_gt_indicator_at("vwap", "5m"), interval="5m")
    assert incompatible_arming_problems(
        entry, available_intervals=frozenset({"5m"})) == []


def test_arming_vwap_daily_blocked_live() -> None:
    """A daily-authored VWAP strategy is internally broken — blocked live."""
    entry = _indicator_entry(_close_gt_indicator_at("vwap", "1d"), interval="1d")
    problems = incompatible_arming_problems(entry)
    assert problems
    assert any("VWAP" in p for p in problems)


def test_arming_vwap_daily_blocked_in_1d_sandbox() -> None:
    entry = _indicator_entry(_close_gt_indicator_at("vwap", "1d"), interval="1d")
    problems = incompatible_arming_problems(
        entry, available_intervals=frozenset({"1d"}))
    assert any("VWAP" in p for p in problems)


def test_arming_ema_daily_clean_in_1d_sandbox() -> None:
    """A daily EMA strategy works fine in a 1d sandbox — not flagged."""
    entry = _indicator_entry(_close_gt_indicator_at("ema", "1d"), interval="1d")
    assert incompatible_arming_problems(
        entry, available_intervals=frozenset({"1d"})) == []


def test_arming_market_entry_never_flagged_in_1d_sandbox() -> None:
    """A MARKET entry has no condition tree → never blocked (it fires on
    the tick regardless of interval). Guards against false positives."""
    assert incompatible_arming_problems(
        _market_entry(), available_intervals=frozenset({"1d"})) == []


def test_arming_pure_builtin_5m_breakout_blocked_in_1d_sandbox() -> None:
    """A 5m builtin-only breakout (no indicators) still needs 5m bars."""
    entry = _indicator_entry(_builtin_breakout_at("5m"), interval="5m")
    problems = incompatible_arming_problems(
        entry, available_intervals=frozenset({"1d"}))
    assert any("5m" in p for p in problems)


def test_arming_pure_builtin_5m_breakout_clean_live() -> None:
    """Live, a 5m builtin breakout is fine — 5m bars are fetchable."""
    entry = _indicator_entry(_builtin_breakout_at("5m"), interval="5m")
    assert incompatible_arming_problems(entry) == []
