"""Sandbox scanner refresh is consumer-gated.

Once the market feed registers the prepared universe, the per-tick
`refresh_scanner_for_sandbox` is no longer a two-symbol scan. Measured on
the ARM64 dev box, `ScanRunner.run` over 500 symbols x 400 bars costs
~96 ms for one scan and ~423 ms for three — per tick, on the Tk thread.
Paying that while the user is on the Chart tab with no scanner-driven
automation armed would make every "next bar" press feel broken for a
result nobody reads.

The gate has exactly two consumers and they have different tolerances:
the Scanner tab only matters when it is viewable, but an armed
`SCANNER_ALERT` entry matters always — those fire from the runner's
`new_rows` subscription, so a skipped scan is a swallowed entry rather
than a missed repaint.

See ``backtest/sandbox_app.spec.md``.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
from typing import Any

from tradinglab.backtest.sandbox_app import SandboxAppController


@contextlib.contextmanager
def _silent_tcl():
    yield


class _ScannerTab:
    def __init__(self, *, viewable: bool = True, scans=("scan-1",)) -> None:
        self._viewable = viewable
        self._scans = list(scans)
        self.results_set = 0

    def winfo_viewable(self) -> int:
        return 1 if self._viewable else 0

    def get_active_scan_definitions(self):
        return list(self._scans)

    def set_results(self, results) -> None:  # noqa: ARG002
        self.results_set += 1


class _Runner:
    def __init__(self) -> None:
        self.runs = 0

    def run(self, **kwargs):  # noqa: ARG002
        self.runs += 1
        return {}


class _Sandbox:
    def __init__(self) -> None:
        self.interval = "5m"
        self.visible_candles_by_symbol = {"AMD": [object()]}

    def current_session_date(self):
        return _dt.date(2026, 6, 10)


class _Evaluator:
    def __init__(self, armed: bool) -> None:
        self._armed = armed

    def has_armed_scanner_alert(self) -> bool:
        return self._armed


class _App:
    def __init__(self, *, viewable: bool, evaluator: Any = None,
                 scans=("scan-1",)) -> None:
        self._scanner_tab = _ScannerTab(viewable=viewable, scans=scans)
        self._scan_runner = _Runner()
        self._scan_tick_id = 0
        self._scan_last_results: dict = {}
        self._entry_evaluator = evaluator


def _ctrl(app_sandbox: _Sandbox | None = None) -> SandboxAppController:
    ctrl = SandboxAppController()
    ctrl.engine = app_sandbox if app_sandbox is not None else _Sandbox()
    return ctrl


def _refresh(ctrl: SandboxAppController, app: _App) -> None:
    ctrl.refresh_scanner_for_sandbox(app=app, silent_tcl=_silent_tcl)


# ---------------------------------------------------------------------------
# Scanner tab visibility
# ---------------------------------------------------------------------------


def test_scans_when_the_scanner_tab_is_visible():
    app = _App(viewable=True)
    _refresh(_ctrl(), app)
    assert app._scan_runner.runs == 1
    assert app._scanner_tab.results_set == 1


def test_skips_when_the_scanner_tab_is_hidden():
    app = _App(viewable=False)
    _refresh(_ctrl(), app)
    assert app._scan_runner.runs == 0


def test_hidden_tab_does_not_burn_a_tick_id():
    """A skipped tick must not advance scan history state."""
    app = _App(viewable=False)
    _refresh(_ctrl(), app)
    assert app._scan_tick_id == 0


def test_unprobeable_geometry_defaults_to_scanning():
    """Headless harness: never silently stop scanning."""
    app = _App(viewable=True)

    def _boom():
        raise RuntimeError("no Tk geometry here")

    app._scanner_tab.winfo_viewable = _boom
    _refresh(_ctrl(), app)
    assert app._scan_runner.runs == 1


# ---------------------------------------------------------------------------
# Armed SCANNER_ALERT entries override visibility
# ---------------------------------------------------------------------------


def test_armed_scanner_alert_forces_a_scan_on_a_hidden_tab():
    app = _App(viewable=False, evaluator=_Evaluator(armed=True))
    _refresh(_ctrl(), app)
    assert app._scan_runner.runs == 1


def test_no_armed_scanner_alert_still_respects_visibility():
    app = _App(viewable=False, evaluator=_Evaluator(armed=False))
    _refresh(_ctrl(), app)
    assert app._scan_runner.runs == 0


def test_stubbed_evaluator_without_the_probe_assumes_armed():
    """Never risk swallowing an entry because a seam is missing."""
    app = _App(viewable=False, evaluator=object())
    _refresh(_ctrl(), app)
    assert app._scan_runner.runs == 1


def test_raising_probe_assumes_armed():
    class _Broken:
        def has_armed_scanner_alert(self):
            raise RuntimeError("boom")

    app = _App(viewable=False, evaluator=_Broken())
    _refresh(_ctrl(), app)
    assert app._scan_runner.runs == 1


# ---------------------------------------------------------------------------
# Pre-existing guards still hold
# ---------------------------------------------------------------------------


def test_no_saved_scans_is_a_no_op():
    app = _App(viewable=True, scans=())
    _refresh(_ctrl(), app)
    assert app._scan_runner.runs == 0


def test_no_session_is_a_no_op():
    app = _App(viewable=True)
    ctrl = SandboxAppController()
    _refresh(ctrl, app)
    assert app._scan_runner.runs == 0


def test_empty_universe_is_a_no_op():
    sb = _Sandbox()
    sb.visible_candles_by_symbol = {}
    app = _App(viewable=True)
    _refresh(_ctrl(sb), app)
    assert app._scan_runner.runs == 0


# ---------------------------------------------------------------------------
# The evaluator probe itself
# ---------------------------------------------------------------------------


def test_evaluator_reports_armed_scanner_alert():
    from tradinglab.entries.evaluator import EntryEvaluator
    from tradinglab.entries.model import TriggerKind

    probe = EntryEvaluator.has_armed_scanner_alert

    class _Trigger:
        def __init__(self, kind):
            self.kind = kind

    class _Strategy:
        def __init__(self, kind):
            self.trigger = _Trigger(kind)

    class _Stub:
        def __init__(self, kinds):
            self._armed = {f"s{i}" for i in range(len(kinds))}
            self._strategies = {
                f"s{i}": _Strategy(k) for i, k in enumerate(kinds)
            }

    assert probe(_Stub([])) is False
    assert probe(_Stub([TriggerKind.MARKET])) is False
    assert probe(_Stub([TriggerKind.SCANNER_ALERT])) is True
    assert probe(_Stub([TriggerKind.MARKET, TriggerKind.SCANNER_ALERT])) is True


def test_evaluator_probe_tolerates_a_dangling_armed_id():
    from tradinglab.entries.evaluator import EntryEvaluator

    class _Stub:
        _armed = {"gone"}
        _strategies: dict = {}

    assert EntryEvaluator.has_armed_scanner_alert(_Stub()) is False
