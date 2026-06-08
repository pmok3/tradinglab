"""GUI test: StrategyTab blocks a Run on an interval-incompatible strategy.

When the selected entry/exit references an intraday-only indicator (VWAP)
and the run interval is daily, clicking Run must show an explanatory
``messagebox.showerror`` popup and NOT start the run (``run_fn`` never
called). On an intraday interval the same strategy runs normally.

Audit ``intraday-interval-guard``. See also
``tests/unit/strategy_tester/test_interval_compat.py`` for the pure
detection-logic contract.
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
from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group  # noqa: E402
from tradinglab.strategy_tester.model import make_run_id  # noqa: E402
from tradinglab.strategy_tester.runner import RunResult  # noqa: E402
from tradinglab.strategy_tester.universe import ResolvedUniverse  # noqa: E402


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


class _FakeStorage:
    def __init__(self, items: list[Any] | None = None) -> None:
        self._items = items or []

    def load_all(self):  # noqa: ANN201
        return self._items, []


def _vwap_entry() -> EntryStrategy:
    """Entry whose INDICATOR trigger references VWAP (close > vwap)."""
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="indicator", id="vwap")},
                interval="5m",
            ),
        ],
    )
    return EntryStrategy(
        id="e1",
        name="VWAP Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("AAPL",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR, condition=cond, interval="5m",
        ),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=1.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _stop_exit() -> ExitStrategy:
    return ExitStrategy(
        id="x1", name="Exit",
        legs=[ExitLeg(id="leg1", triggers=[ExitTrigger(
            kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0)])],
    )


def _stub_run_result() -> RunResult:
    import pathlib
    import tempfile

    from tradinglab.strategy_tester import (
        DatePreset,
        RunStatus,
        TestConfig,
        TestRun,
        UniverseKind,
        UniverseSpec,
    )
    cfg = TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("AAPL",)),
        start_date="2024-01-01",
        end_date="2024-12-31",
        date_preset=DatePreset.CUSTOM,
        starting_cash=100_000.0,
    )
    return RunResult(
        test_run=TestRun(
            run_id=make_run_id(cfg), config=cfg, status=RunStatus.DONE,
            symbol_count_total=1, symbol_count_done=1, trade_count=0,
        ),
        run_dir=pathlib.Path(tempfile.mkdtemp()),
        universe=ResolvedUniverse(symbols=("AAPL",)),
        outcomes=[],
    )


class _MockApp:
    def __init__(self) -> None:
        self._worker_count = 2


def _mount(tk_root, run_fn):
    from tradinglab.gui.strategy_tab import StrategyTab

    tab = StrategyTab(
        tk_root,
        app=_MockApp(),
        entries_storage=_FakeStorage([_vwap_entry()]),
        exits_storage=_FakeStorage([_stop_exit()]),
        watchlists_storage=_FakeStorage(),
        run_fn=run_fn,
        candles_fetcher=lambda sym, interval: [],
    )
    tab.pack(fill="both", expand=True)
    tk_root.update_idletasks()
    if tab._entries:
        tab._var_entry_id.set(tab._cb_entry["values"][0])
    if tab._exits:
        tab._var_exit_id.set(tab._cb_exit["values"][0])
    tk_root.update_idletasks()
    return tab


class TestStrategyTabIntervalGuard:
    def test_daily_run_with_vwap_is_blocked(self, tk_root, monkeypatch) -> None:
        import tradinglab.gui.strategy_tab as st_mod

        run_called = threading.Event()

        def _fake_run(cfg, **kwargs):  # noqa: ANN001, ANN202
            run_called.set()
            return _stub_run_result()

        errors: list[tuple[str, str]] = []
        monkeypatch.setattr(
            st_mod.messagebox, "showerror",
            lambda title, msg, *a, **k: errors.append((title, msg)),
        )

        tab = _mount(tk_root, _fake_run)
        tab._var_interval.set("1d")
        tk_root.update_idletasks()

        tab._on_run_clicked()
        # Give any (erroneously-started) worker a moment to call run.
        time.sleep(0.3)

        assert not run_called.is_set(), (
            "run_fn must NOT be called when an intraday-only indicator is "
            "used on a daily interval"
        )
        assert errors, "an error popup must be shown"
        title, msg = errors[0]
        assert "interval" in title.lower()
        assert "VWAP" in msg
        # The running UI must not have been engaged.
        assert tab._worker is None

    def test_intraday_run_with_vwap_proceeds(self, tk_root, monkeypatch) -> None:
        import tradinglab.gui.strategy_tab as st_mod

        run_called = threading.Event()

        def _fake_run(cfg, **kwargs):  # noqa: ANN001, ANN202
            run_called.set()
            return _stub_run_result()

        errors: list[tuple[str, str]] = []
        monkeypatch.setattr(
            st_mod.messagebox, "showerror",
            lambda title, msg, *a, **k: errors.append((title, msg)),
        )

        tab = _mount(tk_root, _fake_run)
        tab._var_interval.set("5m")
        tk_root.update_idletasks()

        tab._on_run_clicked()
        assert run_called.wait(timeout=5.0), (
            "run_fn must be called for an intraday interval where VWAP is valid"
        )
        assert not errors, f"no error popup expected, got {errors}"
