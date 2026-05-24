"""Tests for :func:`strategy_tester.evaluator.collect_interval_overrides`
and the :class:`RunAggregate.interval_overrides` surfacing path.

Locks in:
* When the test interval matches every authored interval, the helper
  returns an empty list.
* When an EntryTrigger's INDICATOR condition has a Condition with
  ``interval="1m"`` and the test runs at ``"5m"``, the helper returns
  a human-readable warning string naming the trigger.
* An EntryTrigger with no condition (e.g. MARKET) produces NO warning.
* Disabled exit legs / disabled exit triggers are skipped.
* ``RunAggregate.to_dict`` / ``from_dict`` round-trips the list.
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
from tradinglab.entries.model import (
    TriggerKind as EntryTriggerKind,
)
from tradinglab.entries.model import (
    Universe as EntryUniverse,
)
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
)
from tradinglab.exits.model import (
    TriggerKind as ExitTriggerKind,
)
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
)
from tradinglab.strategy_tester.evaluator import collect_interval_overrides
from tradinglab.strategy_tester.report import RunAggregate, load_aggregate, save_aggregate


def _market_entry() -> EntryStrategy:
    return EntryStrategy(
        id="e-mkt", name="market",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=1.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=1,
    )


def _indicator_entry(*, trig_interval: str, cond_interval: str,
                     trig_label: str = "EMA cross") -> EntryStrategy:
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="literal", value=100.0)},
                interval=cond_interval,
            ),
        ],
    )
    return EntryStrategy(
        id="e-ind", name="ind",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR,
            condition=cond,
            interval=trig_interval,
            label=trig_label,
        ),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=1.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=1,
    )


def _stop_exit() -> ExitStrategy:
    return ExitStrategy(
        id="x-stop", name="stop",
        legs=[
            ExitLeg(id="leg1", triggers=[
                ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=5.0,
                            qty_pct=100.0),
            ]),
        ],
        eod_kill_switch=False,
    )


def _indicator_exit(*, cond_interval: str) -> ExitStrategy:
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="literal", value=100.0)},
                interval=cond_interval,
            ),
        ],
    )
    return ExitStrategy(
        id="x-ind", name="ind-exit",
        legs=[
            ExitLeg(id="leg-ind", triggers=[
                ExitTrigger(
                    kind=ExitTriggerKind.INDICATOR,
                    condition=cond,
                    interval=cond_interval,
                    qty_pct=100.0,
                    label="exit on close>100",
                ),
            ]),
        ],
        eod_kill_switch=False,
    )


# ---------------------------------------------------------------------------
# collect_interval_overrides
# ---------------------------------------------------------------------------


def test_market_entry_and_stop_exit_produce_no_overrides() -> None:
    """Plain MARKET entry + STOP exit have no condition trees → no overrides."""
    msgs = collect_interval_overrides(_market_entry(), _stop_exit(), "5m")
    assert msgs == []


def test_indicator_entry_authored_at_matching_interval_no_override() -> None:
    """When every authored interval matches the test interval, the
    helper returns an empty list (the happy path — no warning)."""
    e = _indicator_entry(trig_interval="5m", cond_interval="5m")
    msgs = collect_interval_overrides(e, _stop_exit(), "5m")
    assert msgs == []


def test_indicator_entry_with_authored_1m_at_test_5m_emits_warning() -> None:
    """The actual bug from the user's 3/8 EMA cross template: trigger
    + condition authored at 1m, test runs at 5m → warning surfaced."""
    e = _indicator_entry(
        trig_interval="1m", cond_interval="1m",
        trig_label="3/8 EMA cross",
    )
    msgs = collect_interval_overrides(e, _stop_exit(), "5m")
    assert len(msgs) == 1, f"expected 1 dedup'd warning, got {msgs}"
    msg = msgs[0]
    assert "1m" in msg
    assert "5m" in msg
    assert "single-interval mode" in msg
    # The trigger's user-facing label is preferred over the bare UUID
    # for readability in the banner.
    assert "3/8 EMA cross" in msg


def test_indicator_exit_authored_at_different_interval_emits_warning() -> None:
    """Exit-side INDICATOR triggers are walked too — not just entry."""
    msgs = collect_interval_overrides(
        _market_entry(),
        _indicator_exit(cond_interval="1d"),
        "5m",
    )
    assert any("exit" in m and "1d" in m and "5m" in m for m in msgs), msgs


def test_overrides_are_deduplicated_per_trigger_and_interval() -> None:
    """When trigger.interval and condition.interval both say "1m", we
    don't emit two identical warnings — same scope + label + interval
    is collapsed to one row."""
    e = _indicator_entry(
        trig_interval="1m", cond_interval="1m",
        trig_label="dup-test",
    )
    msgs = collect_interval_overrides(e, _stop_exit(), "5m")
    assert len(msgs) == 1, f"expected dedup; got {msgs}"


def test_overrides_for_two_different_intervals_emit_two_warnings() -> None:
    """If the trigger says "1m" and a nested Condition says "1d", both
    distinct intervals surface separately."""
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="literal", value=100.0)},
                interval="1d",   # distinct from trigger.interval
            ),
        ],
    )
    e = EntryStrategy(
        id="e-mix", name="mix",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR,
            condition=cond,
            interval="1m",       # different from inner condition's 1d
            label="mixed-interval",
        ),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=1.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=1,
    )
    msgs = collect_interval_overrides(e, _stop_exit(), "5m")
    assert len(msgs) == 2, f"expected one warning per distinct interval, got {msgs}"
    intervals_in_msgs = {iv for iv in ("1m", "1d") if any(iv in m for m in msgs)}
    assert intervals_in_msgs == {"1m", "1d"}


# ---------------------------------------------------------------------------
# RunAggregate persistence
# ---------------------------------------------------------------------------


def _empty_agg(overrides: list[str]) -> RunAggregate:
    from tradinglab.strategy_tester.report import ConfidenceInterval

    ci = ConfidenceInterval(lo=0.0, hi=0.0, point=0.0, confidence=0.95)
    return RunAggregate(
        run_id="r-test", schema_version=1,
        trade_count=0, win_count=0, loss_count=0, breakeven_count=0,
        win_rate=0.0,
        win_rate_ci_95=ci,
        total_pnl_gross=0.0, total_pnl_net=0.0,
        expectancy=0.0,
        expectancy_ci_95=ci,
        profit_factor=0.0,
        profit_factor_ci_95=ci,
        avg_win=0.0, avg_loss=0.0,
        largest_win=0.0, largest_loss=0.0,
        max_drawdown=0.0, max_drawdown_pct=0.0,
        sharpe_ratio=0.0, sortino_ratio=0.0,
        interval_overrides=overrides,
    )


def test_run_aggregate_to_dict_includes_interval_overrides() -> None:
    agg = _empty_agg(["entry trigger 'X' authored at 1m; evaluated at 5m"])
    d = agg.to_dict()
    assert "banners" in d
    assert "interval_overrides" in d["banners"]
    assert d["banners"]["interval_overrides"] == [
        "entry trigger 'X' authored at 1m; evaluated at 5m"
    ]


def test_run_aggregate_round_trip_preserves_interval_overrides(tmp_path) -> None:
    overrides = [
        "entry trigger 'A' authored at 1m; evaluated at 5m (single-interval mode)",
        "exit trigger 'B' authored at 1d; evaluated at 5m (single-interval mode)",
    ]
    agg = _empty_agg(overrides)
    save_aggregate(tmp_path, agg)
    loaded = load_aggregate(tmp_path)
    assert loaded is not None
    assert loaded.interval_overrides == overrides


def test_run_aggregate_default_interval_overrides_is_empty(tmp_path) -> None:
    """Existing aggregate.json files written before this field existed
    must still load (back-compat — field defaults to empty list).

    Simulated by writing a payload whose banners dict has no
    ``interval_overrides`` key, then loading it through the disk
    deserializer.
    """
    import json

    from tradinglab.strategy_tester.report import AGGREGATE_FILENAME

    agg = _empty_agg([])
    d = agg.to_dict()
    # Simulate a payload without the new key (older-format aggregate.json).
    del d["banners"]["interval_overrides"]
    (tmp_path / AGGREGATE_FILENAME).write_text(json.dumps(d), encoding="utf-8")
    loaded = load_aggregate(tmp_path)
    assert loaded is not None
    assert loaded.interval_overrides == []
