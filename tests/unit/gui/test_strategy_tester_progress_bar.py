"""Tests for the StrategyTab progress bar widget.

Mounts ``StrategyTab`` headlessly against a real (but off-screen) Tk root,
then exercises the progress bar public API — ``_set_running_ui``,
``_apply_progress``, and ``_on_progress`` — to verify:

* The ``_pbar`` widget exists and is a ``ttk.Progressbar``.
* The bar is hidden initially and shown when a run starts.
* ``_apply_progress(done, total)`` updates both ``value`` and ``maximum``.
* ``_on_progress(test_run)`` (called from a "worker thread" — simulated here
  on the main thread) schedules a ``after(0, ...)`` callback that lands
  correctly after ``update()`` drains the event queue.
* Sequential calls with done=1, 2, 3 out of 3 cause the bar's value to
  reach 3.
"""
from __future__ import annotations

import sys
from typing import Any

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.strategy_tester import (  # noqa: E402
    CostModel,
    DatePreset,
    RunStatus,
    TestConfig,
    TestRun,
    UniverseKind,
    UniverseSpec,
)

# ---------------------------------------------------------------------------
# Tk fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def tk_root():
    """Create a minimal off-screen Tk root; skip if Tk is unavailable."""
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("800x600-3000-3000")  # park off-screen
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStorage:
    """Minimal storage stub — returns empty lists for load_all()."""

    def load_all(self):  # noqa: ANN201
        return [], []


def _cfg() -> TestConfig:
    return TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(
            kind=UniverseKind.SYMBOLS,
            symbols=("AAPL", "MSFT", "NVDA"),
        ),
        start_date="2020-01-01",
        end_date="2024-12-31",
    )


def _make_test_run(done: int, total: int) -> TestRun:
    return TestRun(
        run_id="testrun1",
        config=_cfg(),
        status=RunStatus.RUNNING,
        symbol_count_done=done,
        symbol_count_total=total,
    )


def _make_tab(root: Any):
    """Mount a StrategyTab with all-fake storage; return the widget."""
    from tradinglab.gui.strategy_tab import StrategyTab

    tab = StrategyTab(
        root,
        entries_storage=_FakeStorage(),
        exits_storage=_FakeStorage(),
        watchlists_storage=_FakeStorage(),
    )
    tab.pack(fill="both", expand=True)
    root.update_idletasks()
    return tab


def _drain(root: Any) -> None:
    """Drain after(0, ...) callbacks and idle tasks."""
    root.update()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProgressBarWidget:
    """Verify the progress bar widget structure and initial state."""

    def test_pbar_attribute_exists(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        assert hasattr(tab, "_pbar"), "StrategyTab must expose _pbar"

    def test_pbar_is_ttk_progressbar(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        assert isinstance(tab._pbar, ttk.Progressbar)

    def test_pbar_hidden_initially(self, tk_root: Any) -> None:
        """Progress bar must be grid_remove'd before any run starts."""
        tab = _make_tab(tk_root)
        info = tab._pbar.grid_info()
        assert info == {}, "progress bar should be hidden (no grid_info) initially"

    def test_pbar_mode_is_determinate(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        assert str(tab._pbar["mode"]) == "determinate"


class TestSetRunningUI:
    """Verify show/hide behaviour driven by _set_running_ui."""

    def test_shown_when_running_starts(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()
        info = tab._pbar.grid_info()
        assert info != {}, "progress bar should be visible after _set_running_ui(True)"

    def test_value_reset_to_zero_on_start(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        # Pre-load a non-zero value, then reset.
        tab._set_running_ui(True)
        tab._apply_progress(5, 10)
        tab._set_running_ui(True)  # second run start
        assert tab._pbar["value"] == 0

    def test_run_and_stop_buttons_toggle(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        assert str(tab._btn_run["state"]) == "disabled"
        assert str(tab._btn_stop["state"]) == "normal"

        tab._set_running_ui(False)
        assert str(tab._btn_run["state"]) == "normal"
        assert str(tab._btn_stop["state"]) == "disabled"


class TestApplyProgress:
    """Verify _apply_progress updates bar value, maximum, and status label."""

    def test_value_set_correctly(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()
        tab._apply_progress(3, 10)
        assert tab._pbar["value"] == 3

    def test_maximum_set_correctly(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()
        tab._apply_progress(2, 7)
        assert tab._pbar["maximum"] == 7

    def test_status_label_contains_counts(self, tk_root: Any) -> None:
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()
        tab._apply_progress(4, 12)
        status = tab._var_status.get()
        assert "4" in status
        assert "12" in status

    def test_increments_1_2_3_of_3(self, tk_root: Any) -> None:
        """Bar value reaches 3 after three sequential _apply_progress calls."""
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()

        for done in range(1, 4):
            tab._apply_progress(done, 3)
            assert tab._pbar["value"] == done, (
                f"expected bar value {done} after apply_progress({done}, 3)"
            )

        assert tab._pbar["value"] == 3


class TestOnProgress:
    """Verify _on_progress marshals updates to the Tk thread via after(0, ...)."""

    def test_on_progress_updates_bar_after_drain(self, tk_root: Any) -> None:
        """_on_progress schedules an after(0,...) update; bar value
        reaches 3 once the event loop is drained."""
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()

        # Simulate the runner calling _on_progress (from worker thread in
        # production — called directly here for simplicity).
        for done in range(1, 4):
            test_run = _make_test_run(done=done, total=3)
            tab._on_progress(test_run)
            _drain(tk_root)  # drain after(0, ...) callbacks
            assert tab._pbar["value"] == done, (
                f"expected bar value {done} after on_progress with done={done}"
            )

        assert tab._pbar["value"] == 3

    def test_on_progress_updates_status_label(self, tk_root: Any) -> None:
        """_on_progress updates the status label with symbol counts."""
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()

        test_run = _make_test_run(done=2, total=5)
        tab._on_progress(test_run)
        _drain(tk_root)

        status = tab._var_status.get()
        assert "2" in status
        assert "5" in status

    def test_on_progress_noop_when_zero_total(self, tk_root: Any) -> None:
        """_on_progress with total=0 must not raise (edge-case guard)."""
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()

        test_run = _make_test_run(done=0, total=0)
        tab._on_progress(test_run)  # must not raise
        _drain(tk_root)
        # Bar value stays at 0; no crash.
        assert tab._pbar["value"] == 0


class TestProgressPaintForcing:
    """Verify _apply_progress forces an immediate paint so rapid updates
    are visually distinguishable (regression for "bar jumps from 0 to N/N
    at end of run because Tk batches paint operations")."""

    def test_apply_progress_calls_update_idletasks(self, tk_root: Any) -> None:
        """Each _apply_progress call must invoke _pbar.update_idletasks()
        so the bar visibly advances between rapid sequential updates.

        Without this, when the runner fires progress(test_run) N times
        in <100ms (e.g. cached data, fast strategies), all N after(0, ...)
        callbacks queue and process in a single Tk batch — the bar jumps
        straight from 0 to N/N at the END of the run instead of advancing
        one symbol at a time.
        """
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()

        # Wrap update_idletasks to count calls.
        original = tab._pbar.update_idletasks
        calls: list[float] = []

        def _counting_update():
            calls.append(float(tab._pbar["value"]))
            return original()

        tab._pbar.update_idletasks = _counting_update  # type: ignore[method-assign]

        for done in range(1, 6):
            tab._apply_progress(done, 5)

        assert len(calls) == 5, (
            f"_apply_progress must call update_idletasks once per progress "
            f"update (got {len(calls)} calls for 5 updates)"
        )
        # Each call must have observed the intermediate bar value, not just
        # the final state — proves the paint was forced between updates.
        assert calls == [1.0, 2.0, 3.0, 4.0, 5.0], (
            f"each forced paint must observe the in-flight bar value "
            f"(got {calls}, expected [1.0, 2.0, 3.0, 4.0, 5.0])"
        )

    def test_rapid_after_queue_drains_with_intermediate_paints(
        self, tk_root: Any
    ) -> None:
        """End-to-end repro: queue 12 _on_progress events WITHOUT draining
        between them, drain once, verify update_idletasks was called 12 times.

        This mirrors what the runner does in production — fires progress()
        rapidly from the worker thread, each call queues an after(0, ...)
        on the Tk thread. The fix forces a paint after each, so the user
        sees the bar advance step-by-step.
        """
        tab = _make_tab(tk_root)
        tab._set_running_ui(True)
        tk_root.update_idletasks()

        original = tab._pbar.update_idletasks
        call_count = [0]

        def _counting_update():
            call_count[0] += 1
            return original()

        tab._pbar.update_idletasks = _counting_update  # type: ignore[method-assign]

        # Queue 12 updates without draining between them, as the runner
        # does in production when symbols complete sub-second.
        for done in range(1, 13):
            test_run = _make_test_run(done=done, total=12)
            tab._on_progress(test_run)
        _drain(tk_root)

        assert call_count[0] == 12, (
            f"all 12 queued after(0, ...) callbacks must each force a paint "
            f"(got {call_count[0]} update_idletasks calls)"
        )
        assert tab._pbar["value"] == 12
