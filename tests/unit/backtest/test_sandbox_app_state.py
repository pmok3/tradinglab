"""Multi-layer tests for :mod:`tradinglab.backtest.sandbox_app`.

The :class:`SandboxAppController` is the bridge between the app
toolbar / notebook / status bar and the headless replay engine in
:mod:`backtest.replay` (covered separately in
``test_replay_state_machine.py``). Coverage at baseline is 23%
because all real sandbox usage goes through ``smoke`` tests that
boot a full Tk display.

We test the controller in isolation with a minimal ``_FakeApp`` and
a small ``_FakeSandbox`` mock, exercising:

* Property surface (active / engine / panel / universe / strict_offline).
* ``build_spec`` — translation of dialog payload → :class:`SessionSpec`.
* ``current_result`` / ``current_screenshot_dir`` — fallthrough to
  live engine vs. the stashed last-result snapshot.
* ``can_register`` — strict-offline gating, with and without a
  prepared universe set.
* ``reset_compare_for_session_start`` — pre-session compare blanking.
* ``restore_toolbar_intervals`` — null-safe early returns + toolbar
  unlock dispatch.

No Tk, no matplotlib, no real engine.
"""
from __future__ import annotations

import datetime as _dt
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List

import pytest

from tradinglab.backtest.sandbox_app import SandboxAppController

# ---------------------------------------------------------------------------
# Fixtures and fakes
# ---------------------------------------------------------------------------


@contextmanager
def _no_op_silent_tcl():
    """Stand-in for the app's ``silent_tcl`` context manager."""
    yield


class _RecordingVar:
    """Mimic just enough of a Tk StringVar/BooleanVar for these tests."""

    def __init__(self, value: Any = "") -> None:
        self._value = value
        self.history: list[Any] = [value]

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value
        self.history.append(value)


class _RecordingStatus:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def info(self, msg: str) -> None:
        self.calls.append(("info", msg))

    def warn(self, msg: str) -> None:
        self.calls.append(("warn", msg))

    def error(self, msg: str) -> None:
        self.calls.append(("error", msg))


class _FakeToolbar:
    def __init__(self) -> None:
        self.locked_for: tuple[str, ...] | None = None
        self.unlock_called = False

    def lock_for_sandbox(self, intervals: tuple[str, ...]) -> None:
        self.locked_for = intervals

    def unlock(self) -> None:
        self.unlock_called = True


class _FakeSandbox:
    """Minimal stand-in for :class:`SandboxController`."""

    def __init__(
        self,
        *,
        active: bool = True,
        result_value: Any = None,
        screenshot_dir: Path | None = None,
        interval: str = "5m",
    ) -> None:
        self._active = active
        self._result = result_value
        self.screenshot_dir = screenshot_dir
        self.interval = interval
        self.bars_by_symbol: dict[str, list] = {}
        self.visible_candles_by_symbol: dict[str, list] = {}
        self.result_calls = 0

    def is_active(self) -> bool:
        return self._active

    def result(self):
        self.result_calls += 1
        return self._result


class _FakeApp:
    """Minimal app surface used by SandboxAppController methods."""

    def __init__(self) -> None:
        self.compare_var = _RecordingVar(value=False)
        self.compare_ticker_var = _RecordingVar(value="")
        self.ticker_var = _RecordingVar(value="")
        self.interval_var = _RecordingVar(value="")
        self.source_var = _RecordingVar(value="yfinance")
        self._status = _RecordingStatus()
        self._toolbar = _FakeToolbar()
        self._full_cache: dict[tuple, list] = {}
        self._confirmed_primary_ticker = ""
        self._confirmed_compare_ticker = ""
        self._compare = None
        self.set_data_calls: list[dict] = []
        self.render_calls = 0

    def _set_data_state(self, **kwargs) -> None:
        self.set_data_calls.append(kwargs)

    def _render(self) -> None:
        self.render_calls += 1


# ---------------------------------------------------------------------------
# 1. Property surface
# ---------------------------------------------------------------------------


class TestPropertySurface:
    def test_inactive_when_no_engine(self):
        ctl = SandboxAppController()
        assert ctl.active is False
        assert ctl.engine is None
        assert ctl.last_result is None
        assert ctl.panel is None
        assert ctl.last_screenshot_dir is None

    def test_active_when_engine_is_active(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        assert ctl.active is True

    def test_engine_setter_clears_with_none(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox()
        assert ctl.engine is not None
        ctl.engine = None
        assert ctl.engine is None
        assert ctl.active is False

    def test_last_result_setter_round_trips(self):
        ctl = SandboxAppController()
        sentinel = object()
        ctl.last_result = sentinel
        assert ctl.last_result is sentinel

    def test_universe_round_trips(self):
        ctl = SandboxAppController()
        ctl.universe = frozenset({"AAPL", "MSFT"})
        ctl.universe_id = "test-universe"
        assert ctl.universe == frozenset({"AAPL", "MSFT"})
        assert ctl.universe_id == "test-universe"

    def test_strict_offline_coerces_to_bool(self):
        ctl = SandboxAppController()
        ctl.strict_offline = 1
        assert ctl.strict_offline is True
        ctl.strict_offline = ""
        assert ctl.strict_offline is False

    def test_screenshot_dir_round_trip(self):
        ctl = SandboxAppController()
        p = Path("/tmp/screens")
        ctl.last_screenshot_dir = p
        assert ctl.last_screenshot_dir == p


# ---------------------------------------------------------------------------
# 2. build_spec
# ---------------------------------------------------------------------------


class TestBuildSpec:
    def test_translates_required_fields(self):
        ctl = SandboxAppController()
        result = ctl.build_spec({
            "deck_seed": 42,
            "tickers": ["SPY", "QQQ"],
            "session_date": _dt.date(2025, 4, 29),
            "slippage_bps": 5.0,
            "commission": 1.0,
            "starting_cash": 25_000.0,
            "universe_id": "u-1",
            "universe_symbols": ["SPY", "QQQ"],
            "strict_offline": True,
        })
        assert result.deck_seed == 42
        assert result.tickers == ("SPY", "QQQ")
        assert result.slippage_bps == 5.0
        assert result.commission == 1.0
        assert result.starting_cash == 25_000.0
        assert result.universe_id == "u-1"
        assert result.universe_symbols == ("SPY", "QQQ")
        assert result.strict_offline is True
        # ISO date round-trip.
        assert result.start_clock_iso == "2025-04-29"

    def test_handles_missing_session_date(self):
        ctl = SandboxAppController()
        result = ctl.build_spec({
            "deck_seed": 1,
            "tickers": [],
            "session_date": None,
            "slippage_bps": 0.0,
            "commission": 0.0,
            "starting_cash": 0.0,
        })
        assert result.start_clock_iso == ""
        assert result.tickers == ()

    def test_includes_setup_tags_from_store(self):
        ctl = SandboxAppController()
        # Force the tag store to a known state.
        ctl._tag_store = type(ctl._tag_store)()
        # Drop any builtin tags so the spec reflects user state only.
        try:
            for t in list(ctl._tag_store.list()):
                ctl._tag_store.remove(t)
        except Exception:  # noqa: BLE001
            pass
        result = ctl.build_spec({
            "deck_seed": 1,
            "tickers": [],
            "session_date": None,
            "slippage_bps": 0.0,
            "commission": 0.0,
            "starting_cash": 0.0,
        })
        # setup_tags reflects whatever the tag store currently lists,
        # captured as a tuple in iteration order.
        assert result.setup_tags == tuple(ctl._tag_store.list())


# ---------------------------------------------------------------------------
# 3. current_result / current_screenshot_dir
# ---------------------------------------------------------------------------


class TestCurrentResultAndScreenshotDir:
    def test_current_result_returns_live_when_active(self):
        ctl = SandboxAppController()
        sentinel = object()
        ctl.engine = _FakeSandbox(active=True, result_value=sentinel)
        assert ctl.current_result() is sentinel

    def test_current_result_falls_back_to_last(self):
        ctl = SandboxAppController()
        ctl.last_result = "stashed"
        # No engine ⇒ falls through.
        assert ctl.current_result() == "stashed"

    def test_current_result_returns_last_when_inactive_engine(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=False, result_value="live")
        ctl.last_result = "stashed"
        assert ctl.current_result() == "stashed"

    def test_current_result_handles_engine_exception(self):
        ctl = SandboxAppController()

        class _RaisingSandbox(_FakeSandbox):
            def result(self):
                raise RuntimeError("boom")

        ctl.engine = _RaisingSandbox(active=True)
        assert ctl.current_result() is None

    def test_current_screenshot_dir_live(self):
        ctl = SandboxAppController()
        p = Path("/tmp/live")
        ctl.engine = _FakeSandbox(active=True, screenshot_dir=p)
        assert ctl.current_screenshot_dir() == p

    def test_current_screenshot_dir_falls_back(self):
        ctl = SandboxAppController()
        ctl.last_screenshot_dir = Path("/tmp/last")
        assert ctl.current_screenshot_dir() == Path("/tmp/last")


# ---------------------------------------------------------------------------
# 4. can_register — strict-offline gate matrix
# ---------------------------------------------------------------------------


class TestCanRegister:
    def test_inactive_always_allows(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        # Not active ⇒ unconditionally True.
        assert ctl.can_register(app=app, sym="XYZ") is True

    def test_strict_offline_off_always_allows(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        ctl.strict_offline = False
        ctl.universe = frozenset({"SPY"})
        app = _FakeApp()
        assert ctl.can_register(app=app, sym="MSFT") is True

    def test_strict_offline_empty_universe_allows(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        ctl.strict_offline = True
        ctl.universe = frozenset()
        app = _FakeApp()
        # Empty universe ⇒ degenerate ⇒ allow.
        assert ctl.can_register(app=app, sym="MSFT") is True

    def test_strict_offline_symbol_in_universe_allows(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        ctl.strict_offline = True
        ctl.universe = frozenset({"SPY", "MSFT"})
        app = _FakeApp()
        assert ctl.can_register(app=app, sym="MSFT") is True

    def test_strict_offline_symbol_not_in_universe_blocks_and_logs(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        ctl.strict_offline = True
        ctl.universe = frozenset({"SPY"})
        ctl.universe_id = "prepared-uni"
        app = _FakeApp()
        assert ctl.can_register(app=app, sym="XYZ") is False
        # The error must have surfaced via _status.
        msgs = [m for (level, m) in app._status.calls if level == "error"]
        assert any("XYZ" in m for m in msgs)
        assert any("prepared-uni" in m for m in msgs)

    def test_strict_offline_status_error_swallowed_on_failure(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        ctl.strict_offline = True
        ctl.universe = frozenset({"SPY"})
        app = _FakeApp()

        # Replace _status with one that raises.
        class _BoomStatus:
            def error(self, msg):
                raise RuntimeError("status bar destroyed")

        app._status = _BoomStatus()
        # Must still return False, not propagate.
        assert ctl.can_register(app=app, sym="XYZ") is False


# ---------------------------------------------------------------------------
# 5. reset_compare_for_session_start
# ---------------------------------------------------------------------------


class TestResetCompareForSessionStart:
    def test_clears_compare_and_sets_default(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        app.compare_var.set(True)
        app.compare_ticker_var.set("QQQ")
        ctl.reset_compare_for_session_start(
            app=app, silent_tcl=_no_op_silent_tcl,
            compare_default="IWM",
        )
        assert app.compare_var.get() is False
        assert app.compare_ticker_var.get() == "IWM"
        assert app._confirmed_compare_ticker == "IWM"
        # _set_data_state called with compare=[].
        assert any(call.get("compare") == [] for call in app.set_data_calls)


# ---------------------------------------------------------------------------
# 6. restore_toolbar_intervals
# ---------------------------------------------------------------------------


class TestRestoreToolbarIntervals:
    def test_unlocks_toolbar(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        ctl.restore_toolbar_intervals(app=app, silent_tcl=_no_op_silent_tcl)
        assert app._toolbar.unlock_called is True

    def test_no_op_when_toolbar_missing(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        app._toolbar = None
        # Must not raise.
        ctl.restore_toolbar_intervals(app=app, silent_tcl=_no_op_silent_tcl)


# ---------------------------------------------------------------------------
# 7. restrict_toolbar_intervals
# ---------------------------------------------------------------------------


class TestRestrictToolbarIntervals:
    def test_locks_with_given_intervals(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        ctl.restrict_toolbar_intervals(
            app=app,
            display_intervals=["1m", "5m"],
            daily_available=False,
            silent_tcl=_no_op_silent_tcl,
        )
        assert app._toolbar.locked_for == ("1m", "5m")

    def test_appends_daily_when_available(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        ctl.restrict_toolbar_intervals(
            app=app,
            display_intervals=["5m"],
            daily_available=True,
            silent_tcl=_no_op_silent_tcl,
        )
        assert app._toolbar.locked_for == ("5m", "1d")

    def test_does_not_duplicate_daily_if_present(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        ctl.restrict_toolbar_intervals(
            app=app,
            display_intervals=["5m", "1d"],
            daily_available=True,
            silent_tcl=_no_op_silent_tcl,
        )
        assert app._toolbar.locked_for == ("5m", "1d")

    def test_no_op_when_toolbar_missing(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        app._toolbar = None
        ctl.restrict_toolbar_intervals(
            app=app, display_intervals=["1m"], daily_available=False,
            silent_tcl=_no_op_silent_tcl,
        )


# ---------------------------------------------------------------------------
# 8. install_compare_series
# ---------------------------------------------------------------------------


class TestInstallCompareSeries:
    def test_installs_normalized_symbol_and_flags(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        from tradinglab.models import Candle

        candles = [Candle(
            date=_dt.datetime(2025, 4, 29) + _dt.timedelta(minutes=i * 5),
            open=100, high=101, low=99, close=100.5, volume=1000,
            session="regular",
        ) for i in range(3)]

        # Add the missing _series_cache for the clear() call.
        app._series_cache = {"k": "v"}

        ctl.install_compare_series(
            app=app, symbol=" spy ", candles=candles,
            interval="5m", silent_tcl=_no_op_silent_tcl,
        )
        # Symbol normalized to upper case, stripped.
        assert app.compare_ticker_var.get() == "SPY"
        assert app._confirmed_compare_ticker == "SPY"
        assert app.compare_var.get() is True
        # Compare data state set.
        assert any(call.get("compare") == candles for call in app.set_data_calls)
        # Series cache cleared.
        assert app._series_cache == {}
        # Render attempted.
        assert app.render_calls == 1

    def test_render_failure_logged_via_status(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        app._series_cache = {}

        def _boom():
            raise RuntimeError("render kaboom")

        app._render = _boom
        # Must NOT propagate — error is caught and surfaced.
        ctl.install_compare_series(
            app=app, symbol="SPY", candles=[],
            interval="5m", silent_tcl=_no_op_silent_tcl,
        )
        errors = [m for (lvl, m) in app._status.calls if lvl == "error"]
        assert any("render kaboom" in e for e in errors)


# ---------------------------------------------------------------------------
# 9. install_primary_series (sandbox primary install)
# ---------------------------------------------------------------------------


class TestInstallPrimarySeries:
    def _wire_app(self) -> _FakeApp:
        app = _FakeApp()
        app._cancel_background_fetch_jobs = lambda: None
        app._series_cache = {"x": "y"}

        class _FakeIndicatorCache:
            def __init__(self):
                self.cleared = False

            def clear(self):
                self.cleared = True

        app._indicator_cache = _FakeIndicatorCache()
        app._panel_state = {"primary": {"price_ax": None}}
        app._sandbox_full_session_xlim = None
        app._preserve_xlim_on_render = False
        app._slide_xlim_to_right_edge = True

        class _FakeCanvas:
            def draw_idle(self):
                pass

        app._canvas = _FakeCanvas()
        app._autoscale_y_to_visible = lambda: None
        return app

    def test_clears_caches_and_renders(self):
        ctl = SandboxAppController()
        app = self._wire_app()
        from tradinglab.models import Candle
        candles = [Candle(
            date=_dt.datetime(2025, 4, 29) + _dt.timedelta(minutes=i * 5),
            open=100, high=101, low=99, close=100.5, volume=1000,
            session="regular",
        ) for i in range(3)]
        ctl.install_primary_series(
            app=app, symbol="SPY", candles=candles, interval="5m",
            full_session_length=None, silent_tcl=_no_op_silent_tcl,
        )
        assert app.ticker_var.get() == "SPY"
        assert app.interval_var.get() == "5m"
        assert app._confirmed_primary_ticker == "SPY"
        assert app._series_cache == {}
        assert app._indicator_cache.cleared is True
        assert app.render_calls == 1
        # full_session_length None ⇒ xlim sentinel cleared.
        assert app._sandbox_full_session_xlim is None

    def test_render_failure_returns_without_xlim_setup(self):
        ctl = SandboxAppController()
        app = self._wire_app()

        def _boom():
            raise RuntimeError("render kaboom")

        app._render = _boom
        ctl.install_primary_series(
            app=app, symbol="SPY", candles=[], interval="5m",
            full_session_length=100, silent_tcl=_no_op_silent_tcl,
        )
        # Error reported, xlim sentinel UNCHANGED (early return).
        errors = [m for (lvl, m) in app._status.calls if lvl == "error"]
        assert any("render kaboom" in e for e in errors)


# ---------------------------------------------------------------------------
# 10. reset_scanner_state
# ---------------------------------------------------------------------------


class TestResetScannerState:
    def test_resets_runner_and_tab(self):
        ctl = SandboxAppController()
        app = _FakeApp()

        class _FakeRunner:
            def __init__(self):
                self.reset_called = False

            def reset_history(self):
                self.reset_called = True

        class _FakeScannerTab:
            def __init__(self):
                self.set_results_calls = []

            def set_results(self, r):
                self.set_results_calls.append(r)

        app._scan_runner = _FakeRunner()
        app._scanner_tab = _FakeScannerTab()
        app._scan_tick_id = 99
        app._scan_last_results = {"AAA": "x"}

        ctl.reset_scanner_state(app=app, silent_tcl=_no_op_silent_tcl)
        assert app._scan_runner.reset_called is True
        assert app._scan_tick_id == 0
        assert app._scan_last_results == {}
        assert app._scanner_tab.set_results_calls == [{}]

    def test_swallows_runner_exception(self):
        ctl = SandboxAppController()
        app = _FakeApp()

        class _Boom:
            def reset_history(self):
                raise RuntimeError("nope")

        app._scan_runner = _Boom()
        app._scanner_tab = None
        app._scan_tick_id = 5
        app._scan_last_results = {"x": 1}
        # Must NOT propagate.
        ctl.reset_scanner_state(app=app, silent_tcl=_no_op_silent_tcl)
        assert app._scan_tick_id == 0
        assert app._scan_last_results == {}


# ---------------------------------------------------------------------------
# 11. maybe_write_resume_metadata — early-return guards
# ---------------------------------------------------------------------------


class TestMaybeWriteResumeMetadata:
    def test_no_engine_is_noop(self):
        ctl = SandboxAppController()
        # Must not raise; nothing to write.
        ctl.maybe_write_resume_metadata()

    def test_inactive_engine_is_noop(self):
        ctl = SandboxAppController()
        sb = _FakeSandbox(active=False)
        sb.active = False
        ctl.engine = sb
        ctl.maybe_write_resume_metadata()

    def test_active_engine_without_spec_is_noop(self):
        """Engine reports active but has no ``.engine.spec`` — falls
        through silently."""
        ctl = SandboxAppController()

        class _Sb:
            active = True
            engine = None  # no inner engine ⇒ early return

            def is_active(self):
                return True

        ctl.engine = _Sb()
        ctl.maybe_write_resume_metadata()


# ---------------------------------------------------------------------------
# 12. refresh_scanner_for_sandbox — early-return guards
# ---------------------------------------------------------------------------


class TestRefreshScannerForSandbox:
    def test_no_scanner_tab_is_noop(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        app = _FakeApp()
        app._scanner_tab = None
        app._scan_runner = object()
        # Must not raise.
        ctl.refresh_scanner_for_sandbox(app=app, silent_tcl=_no_op_silent_tcl)

    def test_no_runner_is_noop(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        app = _FakeApp()
        app._scanner_tab = object()
        app._scan_runner = None
        ctl.refresh_scanner_for_sandbox(app=app, silent_tcl=_no_op_silent_tcl)

    def test_no_sandbox_is_noop(self):
        ctl = SandboxAppController()
        # No engine.
        app = _FakeApp()
        app._scanner_tab = object()
        app._scan_runner = object()
        ctl.refresh_scanner_for_sandbox(app=app, silent_tcl=_no_op_silent_tcl)

    def test_no_active_scans_is_noop(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        app = _FakeApp()

        class _ScannerTab:
            def get_active_scan_definitions(self):
                return []

        app._scanner_tab = _ScannerTab()
        app._scan_runner = object()
        # Empty scans list ⇒ early return.
        ctl.refresh_scanner_for_sandbox(app=app, silent_tcl=_no_op_silent_tcl)

    def test_no_visible_candles_is_noop(self):
        ctl = SandboxAppController()
        sb = _FakeSandbox(active=True)
        sb.visible_candles_by_symbol = {}  # empty
        ctl.engine = sb
        app = _FakeApp()

        class _ScannerTab:
            def get_active_scan_definitions(self):
                return [object()]

            def set_results(self, r):
                pass

        app._scanner_tab = _ScannerTab()
        app._scan_runner = object()
        app._scan_tick_id = 0
        # Must not raise — falls through the empty-candles guard.
        ctl.refresh_scanner_for_sandbox(app=app, silent_tcl=_no_op_silent_tcl)


# ---------------------------------------------------------------------------
# 13. register_compare — gating paths
# ---------------------------------------------------------------------------


class TestRegisterCompareGates:
    def test_inactive_returns_false(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        assert ctl.register_compare(
            app=app, symbol="SPY", silent_tcl=_no_op_silent_tcl,
        ) is False

    def test_empty_symbol_returns_false(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        app = _FakeApp()
        assert ctl.register_compare(
            app=app, symbol="   ", silent_tcl=_no_op_silent_tcl,
        ) is False

    def test_strict_offline_blocks_unknown_symbol(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        ctl.strict_offline = True
        ctl.universe = frozenset({"SPY"})
        ctl.universe_id = "uni"
        app = _FakeApp()
        assert ctl.register_compare(
            app=app, symbol="XYZ", silent_tcl=_no_op_silent_tcl,
        ) is False


# ---------------------------------------------------------------------------
# 14. register_and_focus — gating paths
# ---------------------------------------------------------------------------


class TestRegisterAndFocusGates:
    def test_inactive_returns_false(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        assert ctl.register_and_focus(app=app, symbol="SPY") is False

    def test_empty_symbol_returns_false(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        app = _FakeApp()
        assert ctl.register_and_focus(app=app, symbol="  ") is False

    def test_strict_offline_blocks(self):
        ctl = SandboxAppController()
        ctl.engine = _FakeSandbox(active=True)
        ctl.strict_offline = True
        ctl.universe = frozenset({"SPY"})
        ctl.universe_id = "uni"
        app = _FakeApp()
        assert ctl.register_and_focus(app=app, symbol="XYZ") is False

    def test_unknown_source_returns_false(self):
        ctl = SandboxAppController()
        sb = _FakeSandbox(active=True, interval="5m")
        sb.bars_by_symbol = {}  # not registered
        ctl.engine = sb
        app = _FakeApp()
        app.source_var.set("nonexistent_source")
        assert ctl.register_and_focus(app=app, symbol="SPY") is False
        errors = [m for (lvl, m) in app._status.calls if lvl == "error"]
        assert any("no fetcher" in m for m in errors)

    def test_already_registered_jumps_to_set_focus(self):
        ctl = SandboxAppController()
        sb = _FakeSandbox(active=True, interval="5m")
        sb.bars_by_symbol = {"SPY": []}
        sb.focus_calls = []
        sb.set_focus = lambda sym: sb.focus_calls.append(sym)
        ctl.engine = sb
        app = _FakeApp()
        assert ctl.register_and_focus(app=app, symbol="spy") is True
        assert sb.focus_calls == ["SPY"]


# ---------------------------------------------------------------------------
# 15. hide_panel — early-return when no panel
# ---------------------------------------------------------------------------


class TestHidePanel:
    def test_no_panel_no_notebook_is_noop(self):
        ctl = SandboxAppController()
        app = _FakeApp()
        app._notebook = None
        app._sandbox_tab_frame = None
        ctl.hide_panel(app=app, silent_tcl=_no_op_silent_tcl)
        assert ctl.panel is None
