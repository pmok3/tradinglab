"""Tests that StrategyTab passes ChartApp._worker_count to runner.run.

Mounts ``StrategyTab`` headlessly with a mock ChartApp that has
``_worker_count = 12``, stubs ``runner.run`` to capture its kwargs,
clicks Run, and asserts the captured ``max_workers`` kwarg is 12.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.entries.model import (  # noqa: E402
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import TriggerKind as EntryTriggerKind  # noqa: E402
from tradinglab.entries.model import Universe as EntryUniverse  # noqa: E402
from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger  # noqa: E402
from tradinglab.exits.model import TriggerKind as ExitTriggerKind  # noqa: E402
from tradinglab.strategy_tester import (  # noqa: E402
    AcceptanceToken,
    CostModel,
    DatePreset,
    RunStatus,
    TestConfig,
    TestRun,
    UniverseKind,
    UniverseSpec,
)
from tradinglab.strategy_tester.model import make_run_id  # noqa: E402
from tradinglab.strategy_tester.runner import RunResult  # noqa: E402
from tradinglab.strategy_tester.universe import ResolvedUniverse  # noqa: E402

# ---------------------------------------------------------------------------
# Tk fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def tk_root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("900x600-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeStorage:
    def __init__(self, items: list[Any] | None = None) -> None:
        self._items = items or []

    def load_all(self):  # noqa: ANN201
        return self._items, []


def _make_entry() -> EntryStrategy:
    return EntryStrategy(
        id="e1",
        name="TestEntry",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY,
            qty=1.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _make_exit() -> ExitStrategy:
    return ExitStrategy(
        id="x1",
        name="TestExit",
        legs=[
            ExitLeg(
                id="leg1",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.STOP,
                        offset_pct=5.0,
                        qty_pct=100.0,
                    )
                ],
            )
        ],
    )


def _stub_cfg() -> TestConfig:
    return TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("AAPL",)),
        start_date="2024-01-01",
        end_date="2024-12-31",
        date_preset=DatePreset.CUSTOM,
        starting_cash=100_000.0,
    )


def _stub_run_result() -> RunResult:
    cfg = _stub_cfg()
    run_id = make_run_id(cfg)
    test_run = TestRun(
        run_id=run_id,
        config=cfg,
        status=RunStatus.DONE,
        symbol_count_total=1,
        symbol_count_done=1,
        trade_count=0,
    )
    import pathlib
    import tempfile
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    return RunResult(
        test_run=test_run,
        run_dir=tmp_dir,
        universe=ResolvedUniverse(symbols=("AAPL",)),
        outcomes=[],
    )


class _MockApp:
    """Minimal ChartApp stand-in with _worker_count."""

    def __init__(self, worker_count: int) -> None:
        self._worker_count = worker_count


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestStrategyTabWorkerCount:
    """StrategyTab must forward ChartApp._worker_count to runner.run."""

    def test_worker_count_forwarded_to_run(self, tk_root: Any) -> None:
        """With _worker_count=12 on mock app, runner.run receives max_workers=12."""
        from tradinglab.gui.strategy_tab import StrategyTab

        captured_kwargs: dict[str, Any] = {}
        run_called = threading.Event()

        def _fake_run(cfg, **kwargs):  # noqa: ANN001, ANN202
            captured_kwargs.update(kwargs)
            run_called.set()
            return _stub_run_result()

        entry = _make_entry()
        exit_ = _make_exit()

        mock_app = _MockApp(worker_count=12)

        tab = StrategyTab(
            tk_root,
            app=mock_app,
            entries_storage=_FakeStorage([entry]),
            exits_storage=_FakeStorage([exit_]),
            watchlists_storage=_FakeStorage(),
            run_fn=_fake_run,
            candles_fetcher=lambda sym, interval: [],
        )
        tab.pack(fill="both", expand=True)
        tk_root.update_idletasks()

        # Select entry + exit in the comboboxes
        if tab._entries:
            tab._var_entry_id.set(tab._cb_entry["values"][0])
        if tab._exits:
            tab._var_exit_id.set(tab._cb_exit["values"][0])
        tk_root.update_idletasks()

        # Fire the Run button
        tab._on_run_clicked()

        # Wait for the worker thread to invoke _fake_run (up to 5 s)
        assert run_called.wait(timeout=5.0), "runner.run was never called after clicking Run"

        assert "max_workers" in captured_kwargs, (
            "StrategyTab must pass max_workers= to runner.run"
        )
        assert captured_kwargs["max_workers"] == 12, (
            f"Expected max_workers=12, got {captured_kwargs['max_workers']!r}"
        )
