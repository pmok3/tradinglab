"""Sandbox menu orchestration with explicit controller and dialog boundaries."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tradinglab.gui import sandbox_menu
from tradinglab.gui.sandbox_menu import SandboxMenuMixin


class _App(SandboxMenuMixin):
    def __init__(self):
        self._sandbox = None
        self._status = Mock(spec=["info", "warn", "error"])
        self.source_var = SimpleNamespace(get=lambda: "chart")
        self.interval_var = SimpleNamespace(get=lambda: "1d")
        self.prepost_var = SimpleNamespace(get=lambda: False)
        self._full_cache = {}
        self._sandbox_tag_store = object()
        self._sandbox_ctrl = Mock(spec=["start_feed", "stop_feed"])
        self._sandbox_universe = frozenset()
        self._sandbox_universe_id = ""
        self._sandbox_strict_offline = False
        self._confirmed_primary_ticker = " amd "
        self._last_sandbox_result = object()
        self._last_sandbox_screenshot_dir = Path("previous-shots")
        self.wait_window = Mock()
        self._build_sandbox_spec = Mock(return_value=SimpleNamespace(starting_cash=25_000))
        self._confirm_sandbox_data_ready = Mock(return_value=True)
        self._sandbox_screenshot_dir = Mock(return_value=Path("session-shots"))
        self._show_sandbox_panel = Mock()
        self._hide_sandbox_panel = Mock()
        self._restrict_toolbar_intervals_for_sandbox = Mock()
        self._restore_toolbar_intervals_from_sandbox = Mock()
        self._reset_scanner_state = Mock()
        self._refresh_watchlist_for_sandbox = Mock()
        self._sandbox_register_and_focus = Mock()
        self._chartstack = Mock(spec=["attach_sandbox"])
        self._indicator_dialog = Mock(spec=["refresh_available_intervals"])
        self._preload_watchlist = Mock()
        self._preload_watchlist_daily = Mock()
        self._populate_watchlist_tab = Mock()
        self._current_sandbox_result = Mock(return_value=self._last_sandbox_result)
        self._current_sandbox_screenshot_dir = Mock(return_value=self._last_sandbox_screenshot_dir)

    def _is_sandbox_active(self):
        return self._sandbox is not None


@pytest.fixture
def env(monkeypatch):
    from tradinglab import defaults, settings
    from tradinglab.backtest import persistence, replay
    from tradinglab.data import base
    from tradinglab.gui import performance_view, sandbox_dialog, universe_prepare_dialog

    prefs = {"sandbox_reference_symbol": " spy ", "sandbox_data_source": ""}
    monkeypatch.setattr(defaults, "get", lambda key: prefs.get(key))
    monkeypatch.setattr(defaults, "reload", Mock())
    monkeypatch.setattr(settings, "get", lambda key, default=None: prefs.get(key, default))
    save_setting = Mock(side_effect=prefs.__setitem__)
    monkeypatch.setattr(settings, "set", save_setting)
    monkeypatch.setattr(base, "user_visible_sources", lambda: ["chart", "replay"])
    monkeypatch.setattr(sandbox_menu._quality, "preferred_source", lambda *a, **k: "replay")
    monkeypatch.setattr(sandbox_menu._quality, "partial_volume_warning", lambda src: None)
    bars = [object()]
    fetch = Mock(return_value=bars)
    monkeypatch.setattr(sandbox_menu, "DATA_SOURCES", {"replay": fetch})
    result = dict(interval="5m", session_date=date(2026, 6, 10), lookback_days=2,
                  daily_lookback_bars=0, display_intervals=["5m", "15m"], data_source="replay")
    dialog = Mock(return_value=SimpleNamespace(result=result))
    monkeypatch.setattr(sandbox_dialog, "SandboxStartDialog", dialog)
    controller = Mock(spec=["start_session", "end_session", "session_id", "screenshot_dir"])
    controller.session_id = "session-id"
    controller.screenshot_dir = Path("session-shots")
    factory = Mock(return_value=controller)
    monkeypatch.setattr(replay, "SandboxController", factory)
    perf = Mock(return_value=Mock(spec=["lift"]))
    monkeypatch.setattr(performance_view, "PerformanceView", perf)
    save = Mock(return_value=Path("saved.json"))
    load = Mock()
    monkeypatch.setattr(persistence, "save_session", save)
    monkeypatch.setattr(persistence, "load_session", load)
    prepare = Mock(return_value=SimpleNamespace(result=None))
    monkeypatch.setattr(universe_prepare_dialog, "UniversePrepareDialog", prepare)
    return SimpleNamespace(app=_App(), result=result, prefs=prefs, bars=bars, fetch=fetch,
                           dialog=dialog, controller=controller, factory=factory, perf=perf,
                           save=save, load=load, prepare=prepare, save_setting=save_setting)


def test_start_cancellation_has_no_fetch_settings_or_controller_side_effects(env):
    env.dialog.return_value.result = None
    env.app._on_menu_sandbox_start()
    env.app.wait_window.assert_called_once_with(env.dialog.return_value)
    env.fetch.assert_not_called()
    env.factory.assert_not_called()
    env.save_setting.assert_not_called()
    env.app._build_sandbox_spec.assert_not_called()
    assert env.app._sandbox is None
    assert env.dialog.call_args.kwargs["reference_symbol"] == "SPY"
    assert env.dialog.call_args.kwargs["default_interval"] == "1m"


@pytest.mark.parametrize("action", ["_on_menu_sandbox_start", "_on_menu_sandbox_prepare_universe"])
def test_active_session_blocks_start_and_download_without_mutation(env, action):
    env.app._sandbox = env.controller
    getattr(env.app, action)()
    assert env.app._sandbox is env.controller
    env.dialog.assert_not_called()
    env.prepare.assert_not_called()
    env.fetch.assert_not_called()
    assert "active" in env.app._status.info.call_args.args[0]


@pytest.mark.parametrize("failure,fragment", [
    ("missing-source", "no fetcher configured"),
    ("empty", "no SPY bars"), ("error", "offline"),
])
def test_reference_failure_aborts_before_controller_and_data_gate(env, monkeypatch, failure, fragment):
    if failure == "missing-source":
        monkeypatch.setattr(sandbox_menu, "DATA_SOURCES", {})
    elif failure == "empty":
        env.fetch.return_value = []
    else:
        env.fetch.side_effect = OSError("offline")
    env.app._on_menu_sandbox_start()
    assert env.app._sandbox is None
    env.factory.assert_not_called()
    env.app._confirm_sandbox_data_ready.assert_not_called()
    env.app._show_sandbox_panel.assert_not_called()
    assert fragment in env.app._status.error.call_args.args[0]


def test_start_offline_gate_precedes_controller_construction(env):
    env.result["universe_symbols"] = ["AMD", "NVDA"]
    env.app._confirm_sandbox_data_ready.return_value = False
    env.app._on_menu_sandbox_start()
    env.app._confirm_sandbox_data_ready.assert_called_once_with(
        source="replay", interval="5m", universe_symbols=("AMD", "NVDA"))
    env.factory.assert_not_called()
    assert env.app._sandbox is None
    assert env.app._sandbox_universe == frozenset()
    env.app._show_sandbox_panel.assert_not_called()


def test_start_uses_cached_reference_pins_source_and_canonicalizes_universe(env):
    env.app._full_cache[("replay", "SPY", "5m")] = env.bars
    env.result.update(universe_symbols=["^VIX", "$VIX", "amd", ""],
                      universe_id="  prepared  ", strict_offline=True)
    env.app._on_menu_sandbox_start()
    env.fetch.assert_not_called()
    env.factory.assert_called_once_with(app=env.app, tag_store=env.app._sandbox_tag_store)
    kwargs = env.controller.start_session.call_args.kwargs
    assert kwargs["reference_symbol"] == "SPY"
    assert kwargs["reference_candles"] == env.bars
    assert kwargs["data_source"] == "replay"
    assert kwargs["include_extended"] is False
    assert kwargs["session_date"] == date(2026, 6, 10)
    assert kwargs["lookback_days"] == 2
    assert kwargs["display_intervals"] == ["5m", "15m"]
    assert env.app._sandbox_universe == frozenset({"VIX", "AMD", "SPY"})
    assert env.app._sandbox_universe_id == "prepared"
    assert env.app._sandbox_strict_offline
    env.app._restrict_toolbar_intervals_for_sandbox.assert_called_once_with(
        display_intervals=["5m", "15m"], daily_available=False)
    env.app._sandbox_register_and_focus.assert_called_once_with("AMD")
    env.app._chartstack.attach_sandbox.assert_called_once_with(env.controller)
    env.app._sandbox_ctrl.start_feed.assert_called_once_with(app=env.app)
    env.app._indicator_dialog.refresh_available_intervals.assert_called_once()
    env.save_setting.assert_called_once_with("sandbox_data_source", "replay")


def test_daily_fetch_failure_and_empty_auto_cycle_degrade_without_blocking_start(env):
    env.result.update(daily_lookback_bars=100, blind=True, auto_cycle=True, eligible_dates=[])
    env.fetch.side_effect = [env.bars, OSError("daily offline")]
    env.app._on_menu_sandbox_start()
    env.fetch.assert_has_calls([call("SPY", "5m"), call("SPY", "1d")])
    assert env.app._sandbox is env.controller
    kwargs = env.controller.start_session.call_args.kwargs
    assert kwargs["daily_reference_candles"] == []
    assert kwargs["auto_cycle"] is False and kwargs["blind"] is False
    warnings = [c.args[0] for c in env.app._status.warn.call_args_list]
    assert any("daily offline" in msg for msg in warnings)
    assert any("single-day" in msg for msg in warnings)


def test_failed_controller_start_does_not_install_universe_or_panel(env):
    env.result.update(universe_symbols=["AMD"], universe_id="prepared", strict_offline=True)
    env.controller.start_session.side_effect = ValueError("invalid session")
    env.app._on_menu_sandbox_start()
    assert env.app._sandbox is None
    assert env.app._sandbox_universe == frozenset()
    assert not env.app._sandbox_strict_offline
    env.app._show_sandbox_panel.assert_not_called()
    env.app._sandbox_ctrl.start_feed.assert_not_called()
    env.app._restrict_toolbar_intervals_for_sandbox.assert_not_called()
    assert "invalid session" in env.app._status.error.call_args.args[0]


@pytest.mark.parametrize("end_fails", [False, True])
def test_end_stops_feed_clears_replay_only_state_and_repairs_watchlist(env, end_fails):
    app = env.app
    app._sandbox = env.controller
    app._sandbox_universe = frozenset({"AMD"})
    app._sandbox_universe_id = "prepared"
    app._sandbox_strict_offline = True
    app._sandbox_full_session_xlim = (0, 100)
    app._preserve_xlim_on_render = True
    app._watchlist_snapshot = {
        "AMD": dict(last=100, change_1d=5, pct_1d=5, chg=5, pct=5, sector="Tech"),
        "pending": None,
    }
    old_result = app._last_sandbox_result
    ended = object()
    sequence = []
    app._sandbox_ctrl.stop_feed.side_effect = lambda: sequence.append("stop")

    def end():
        sequence.append("end")
        if end_fails:
            raise RuntimeError("end failed")
        return ended

    env.controller.end_session.side_effect = end
    app._hide_sandbox_panel.side_effect = RuntimeError("panel already destroyed")
    app._on_menu_sandbox_end()
    assert sequence == ["stop", "end"]
    assert app._last_sandbox_result is (old_result if end_fails else ended)
    assert app._last_sandbox_screenshot_dir == Path("previous-shots" if end_fails else "session-shots")
    assert app._sandbox is None
    assert app._sandbox_universe == frozenset()
    assert app._sandbox_universe_id == ""
    assert not app._sandbox_strict_offline
    assert app._sandbox_full_session_xlim is None
    assert app._preserve_xlim_on_render is False
    assert app._watchlist_snapshot == {"AMD": {"sector": "Tech"}, "pending": None}
    app._restore_toolbar_intervals_from_sandbox.assert_called_once()
    app._reset_scanner_state.assert_called_once()
    app._preload_watchlist.assert_called_once()
    app._preload_watchlist_daily.assert_called_once()
    app._populate_watchlist_tab.assert_called_once()
    app._indicator_dialog.refresh_available_intervals.assert_called_once()
    assert "panel already destroyed" in app._status.error.call_args.args[0]
    app._on_menu_sandbox_end()
    env.controller.end_session.assert_called_once()


@pytest.mark.parametrize("action,picker,operation", [
    ("_on_menu_sandbox_save", "asksaveasfilename", "save"),
    ("_on_menu_sandbox_load", "askopenfilename", "load"),
])
def test_file_picker_cancellation_does_not_touch_session(env, monkeypatch, action, picker, operation):
    from tkinter import filedialog

    monkeypatch.setattr(filedialog, picker, Mock(return_value=""))
    previous = env.app._last_sandbox_result
    getattr(env.app, action)()
    getattr(env, operation).assert_not_called()
    env.perf.assert_not_called()
    assert env.app._last_sandbox_result is previous


@pytest.mark.parametrize("active", [True, False])
def test_load_shows_loaded_result_but_never_overwrites_live_session(env, monkeypatch, tmp_path, active):
    from tkinter import filedialog

    path = tmp_path / "loaded.json"
    monkeypatch.setattr(filedialog, "askopenfilename", Mock(return_value=str(path)))
    loaded = SimpleNamespace(result=SimpleNamespace(post_trades=[object()]), screenshot_dir=tmp_path)
    env.load.return_value = loaded
    old_result = env.app._last_sandbox_result
    env.app._sandbox = env.controller if active else None
    env.app._on_menu_sandbox_load()
    env.load.assert_called_once_with(path)
    assert env.app._last_sandbox_result is (old_result if active else loaded.result)
    assert env.app._sandbox is (env.controller if active else None)
    assert env.perf.call_args.args == (env.app, loaded.result)
    assert env.perf.call_args.kwargs["screenshot_dir"] == tmp_path
    env.perf.return_value.lift.assert_called_once()


def test_load_failure_leaves_prior_result_and_does_not_open_performance(env, monkeypatch, tmp_path):
    from tkinter import filedialog

    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kw: str(tmp_path / "bad.json"))
    env.load.side_effect = ValueError("invalid JSON")
    previous = env.app._last_sandbox_result
    env.app._on_menu_sandbox_load()
    assert env.app._last_sandbox_result is previous
    env.perf.assert_not_called()
    assert "invalid JSON" in env.app._status.error.call_args.args[0]


def test_save_passes_session_identity_and_screenshots_and_surfaces_io_error(env, monkeypatch, tmp_path):
    from tkinter import filedialog

    path = tmp_path / "session.json"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: str(path))
    env.app._sandbox = env.controller
    env.save.side_effect = OSError("disk full")
    env.app._on_menu_sandbox_save()
    env.save.assert_called_once_with(path, env.app._last_sandbox_result, session_id="session-id",
                                     screenshot_dir=env.app._last_sandbox_screenshot_dir)
    assert "disk full" in env.app._status.error.call_args.args[0]
    assert env.app._sandbox is env.controller


def test_prepare_seeds_pinned_source_and_reports_manifest_source(env, monkeypatch):
    from tradinglab import data

    env.prefs["sandbox_data_source"] = "replay"
    monkeypatch.setattr(data, "user_visible_sources", lambda: ["chart", "replay"])
    env.prepare.return_value.result = SimpleNamespace(
        id="prepared", source="other-provider", symbols=["AMD", "SPY"], intervals=["5m"])
    env.app._on_menu_sandbox_prepare_universe()
    kwargs = env.prepare.call_args.kwargs
    assert kwargs["source_name"] == "replay"
    assert kwargs["fetcher"] is env.fetch
    assert kwargs["sources"] == ["chart", "replay"]
    assert kwargs["fetcher_for"]("replay") is env.fetch
    env.app.wait_window.assert_called_once_with(env.prepare.return_value)
    env.fetch.assert_not_called()
    assert "other-provider" in env.app._status.info.call_args.args[0]
    assert "2 symbols across 1 intervals" in env.app._status.info.call_args.args[0]
