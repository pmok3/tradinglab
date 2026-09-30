"""Behavioral regressions for the opt-in strategy timing harness."""

from dataclasses import replace

import pytest

from tests.perf import test_strategy_eval_timing as gate


@pytest.fixture(scope="module", params=sorted(gate._BUDGETS_MS_PER_BAR))
def workload(request):
    candles = gate._synthetic_rth_5m_candles()
    assert gate._TEST_BARS == len(candles) == 8_000
    strategy_id = request.param
    entry = gate._entry_for(strategy_id)
    complete = gate._evaluate(entry, candles)
    truncated = gate._evaluate(entry, candles[:78])
    assert len(truncated.equity_curve) == 78
    assert truncated.post_trades, "truncation must evade the former nonzero-trade guard"
    return strategy_id, candles, complete, truncated


@pytest.mark.parametrize("bad_run", range(gate._TIMING_RUNS + 1))
def test_timing_case_rejects_truncation_in_warmup_or_any_sample(workload, monkeypatch, bad_run):
    strategy_id, candles, complete, truncated = workload
    calls = 0

    def evaluate(entry, supplied_candles):
        nonlocal calls
        assert supplied_candles is candles
        result = truncated if calls == bad_run else complete
        calls += 1
        return result

    monkeypatch.setattr(gate, "_evaluate", evaluate)
    with pytest.raises(AssertionError, match="incomplete workload"):
        gate.test_strategy_eval_per_bar_budget(
            strategy_id, gate._BUDGETS_MS_PER_BAR[strategy_id], candles,
        )
    assert calls == bad_run + 1


def test_standalone_workload_guard_rejects_real_truncation(workload, monkeypatch):
    strategy_id, candles, complete, truncated = workload

    def evaluate(entry, supplied_candles):
        assert supplied_candles is candles
        return truncated if entry.name == gate._entry_for(strategy_id).name else complete

    monkeypatch.setattr(gate, "_evaluate", evaluate)
    with pytest.raises(AssertionError, match="incomplete workload"):
        gate.test_eval_workloads_produce_trades(candles)


@pytest.mark.parametrize(
    "defect,message",
    [
        ("short-fixture", "fixed 8,000-bar workload"),
        ("wrong-end", "final input timestamp"),
        ("no-trades", "multiple ET sessions"),
        ("one-session", "multiple ET sessions"),
    ],
)
def test_complete_workload_contract(workload, defect, message):
    strategy_id, candles, complete, truncated = workload
    result = complete
    if defect == "short-fixture":
        candles, result = candles[:78], truncated
    elif defect == "wrong-end":
        result = replace(complete, equity_curve=complete.equity_curve[:-1] + complete.equity_curve[:1])
    elif defect == "no-trades":
        result = replace(complete, post_trades=[])
    elif defect == "one-session":
        result = replace(complete, post_trades=truncated.post_trades * 2)
    with pytest.raises(AssertionError, match=message):
        gate._assert_complete_workload(strategy_id, candles, result)


def test_each_result_is_checked_outside_timing(workload, monkeypatch):
    strategy_id, candles, complete, _ = workload
    events = []
    real_check = gate._assert_complete_workload

    def evaluate(entry, supplied_candles):
        events.append("evaluate")
        return complete

    def clock():
        events.append("clock")
        return 0.0

    def check(strategy, supplied_candles, result):
        events.append("check")
        assert result is complete
        real_check(strategy, supplied_candles, result)

    monkeypatch.setattr(gate, "_evaluate", evaluate)
    monkeypatch.setattr(gate.time, "perf_counter", clock)
    monkeypatch.setattr(gate, "_assert_complete_workload", check)
    gate.test_strategy_eval_per_bar_budget(
        strategy_id, gate._BUDGETS_MS_PER_BAR[strategy_id], candles,
    )
    assert events == ["evaluate", "check"] + ["clock", "evaluate", "clock", "check"] * gate._TIMING_RUNS


def test_timing_timeout_allows_full_near_budget_work():
    timeout, = (mark for mark in gate.test_strategy_eval_per_bar_budget.pytestmark if mark.name == "timeout")
    budget_seconds = max(gate._BUDGETS_MS_PER_BAR.values()) * gate._TEST_BARS / 1000
    assert timeout.args[0] > budget_seconds * (gate._TIMING_RUNS + 1)
