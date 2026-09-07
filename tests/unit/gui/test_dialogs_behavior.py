"""Settings transactions and watchlist actions, without constructing ChartApp.

Controller callbacks use explicit fakes. Only the Treeview selection and button
state contract below needs real Tk widgets, under the shared root fixture.
"""
from __future__ import annotations

import tkinter as tk
from copy import deepcopy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tradinglab import defaults, settings
from tradinglab.constants import BUILTIN_STARTUP_DEFAULTS, STARTUP_DEFAULT_KEYS
from tradinglab.gui import dialogs
from tradinglab.gui.dialogs import _SettingsDialog, _WatchlistDialog
from tradinglab.watchlists import WatchlistManager


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture
def settings_dialog(monkeypatch):
    prefs = {"use_colorblind_palette": False, "volume_tod_enabled": False}
    monkeypatch.setattr(settings, "get", lambda key, default=None: prefs.get(key, default))
    write = Mock(side_effect=prefs.__setitem__)
    monkeypatch.setattr(settings, "set", write)
    monkeypatch.setattr(defaults, "get", lambda key: prefs.get(key))
    monkeypatch.setattr(defaults, "reload", Mock())
    app = SimpleNamespace(
        dark_var=_Var(False), log_price_var=_Var(False),
        ticker_var=_Var("AMD"), compare_ticker_var=_Var("SPY"),
        interval_var=_Var("5m"), source_var=_Var("fixture"),
        _worker_count=4, _scroll_zoom_invert=False, _drawings_snap_to_ohlc=False,
        _ui_scale=1.0, _display_tz="", _theme_overrides={"dark": {"text": "#ffffff"}},
        _startup_defaults=dict(BUILTIN_STARTUP_DEFAULTS),
        _apply_theme=Mock(), _apply_price_scale=Mock(),
    )
    for method, attribute in [
        ("set_ui_scale", "_ui_scale"), ("set_worker_count", "_worker_count"),
        ("set_display_tz", "_display_tz"), ("set_scroll_zoom_invert", "_scroll_zoom_invert"),
        ("set_drawings_snap_to_ohlc", "_drawings_snap_to_ohlc"),
        ("replace_theme_overrides", "_theme_overrides"),
        ("replace_startup_defaults", "_startup_defaults"),
    ]:
        setattr(app, method, Mock(side_effect=lambda value, attr=attribute: setattr(app, attr, deepcopy(value))))
    app.set_startup_default = Mock(side_effect=app._startup_defaults.__setitem__)
    app.set_use_colorblind_palette = Mock(side_effect=lambda v: prefs.__setitem__("use_colorblind_palette", v))
    app.set_volume_tod_enabled = Mock(side_effect=lambda v: prefs.__setitem__("volume_tod_enabled", v))
    dlg = SimpleNamespace(
        _parent_app=app, destroy=Mock(), _worker_var=_Var(4),
        _dark_initial=False, _dark_var=_Var(False), _log_initial=False, _log_var=_Var(False),
        _scroll_invert_initial=False, _scroll_invert_var=_Var(False),
        _snap_ohlc_initial=False, _snap_ohlc_var=_Var(False),
        _ui_scale_initial=1.0, _ui_scale_var=_Var("100%"), _ui_scale_choices=(1.0, 1.15, 1.3),
        _colorblind_initial=False, _colorblind_var=_Var(False),
        _vol_tod_initial=False, _vol_tod_var=_Var(False),
        _tz_initial="", _tz_var=_Var(""),
        _sandbox_ref_initial="SPY", _sandbox_ref_var=_Var("SPY"),
        _skip_journal_initial=False, _skip_journal_var=_Var(False),
        _splash_initial=True, _splash_var=_Var(True),
        _update_check_initial=True, _update_check_var=_Var(True),
        _wl_cap_initial=5, _wl_cap_var=_Var(5),
        _overrides_initial=deepcopy(app._theme_overrides),
        _startup_initial=deepcopy(app._startup_defaults),
        _startup_vars={key: _Var(value) for key, value in app._startup_defaults.items()},
        _parse_ui_scale=_SettingsDialog._parse_ui_scale,
    )
    dlg._commit_startup_defaults = MethodType(_SettingsDialog._commit_startup_defaults, dlg)
    return SimpleNamespace(dlg=dlg, app=app, prefs=prefs, write=write)


def test_cancel_reverts_live_previews_and_discards_uncommitted_fields(settings_dialog):
    env = settings_dialog
    dlg, app = env.dlg, env.app
    for variable, handler in [
        ("_dark_var", "_on_dark_toggle"), ("_log_var", "_on_log_toggle"),
        ("_scroll_invert_var", "_on_scroll_invert_toggle"),
        ("_snap_ohlc_var", "_on_snap_ohlc_toggle"),
        ("_colorblind_var", "_on_colorblind_toggle"), ("_vol_tod_var", "_on_volume_tod_toggle"),
    ]:
        getattr(dlg, variable).set(True)
        getattr(_SettingsDialog, handler)(dlg)
    dlg._ui_scale_var.set("115%")
    _SettingsDialog._on_ui_scale_changed(dlg)
    assert app.dark_var.get() and app.log_price_var.get()
    assert app._scroll_zoom_invert and app._drawings_snap_to_ohlc
    assert app._ui_scale == 1.15
    assert all(env.prefs.values())
    app._theme_overrides["dark"]["text"] = "#000000"
    app._startup_defaults["ticker"] = "NVDA"
    dlg._startup_vars["ticker"].set("MSFT")
    dlg._worker_var.set(16)
    dlg._tz_var.set("UTC")
    dlg._sandbox_ref_var.set("QQQ")
    _SettingsDialog._on_cancel(dlg)
    assert app.dark_var.get() is False and app.log_price_var.get() is False
    assert not app._scroll_zoom_invert and not app._drawings_snap_to_ohlc
    assert app._ui_scale == 1.0
    assert env.prefs == {"use_colorblind_palette": False, "volume_tod_enabled": False}
    assert app._theme_overrides == dlg._overrides_initial
    assert app._startup_defaults == dlg._startup_initial
    app._apply_theme.assert_has_calls([call(), call()])
    app._apply_price_scale.assert_has_calls([call(), call()])
    app.set_startup_default.assert_not_called()
    app.set_worker_count.assert_not_called()
    app.set_display_tz.assert_not_called()
    app.set_scroll_zoom_invert.assert_not_called()
    app.set_drawings_snap_to_ohlc.assert_not_called()
    env.write.assert_not_called()
    dlg.destroy.assert_called_once()


def test_cancel_without_edits_does_not_reapply_or_persist(settings_dialog):
    env = settings_dialog
    _SettingsDialog._on_cancel(env.dlg)
    for method in ("_apply_theme", "_apply_price_scale", "set_ui_scale", "set_use_colorblind_palette",
                   "set_volume_tod_enabled", "replace_theme_overrides", "replace_startup_defaults"):
        getattr(env.app, method).assert_not_called()
    env.write.assert_not_called()
    env.dlg.destroy.assert_called_once()


def test_capture_and_reset_startup_defaults_only_edit_dialog_until_commit(settings_dialog):
    dlg, app = settings_dialog.dlg, settings_dialog.app
    original = dict(app._startup_defaults)
    app.dark_var.set(True)
    _SettingsDialog._on_capture_current_as_default(dlg)
    assert {k: v.get() for k, v in dlg._startup_vars.items()} == {
        "ticker": "AMD", "compare": "SPY", "interval": "5m", "source": "fixture", "theme": "dark"}
    assert app._startup_defaults == original
    _SettingsDialog._on_reset_startup_defaults(dlg)
    assert {k: v.get() for k, v in dlg._startup_vars.items()} == BUILTIN_STARTUP_DEFAULTS
    app.set_startup_default.assert_not_called()
    dlg._startup_vars["ticker"].set("NVDA")
    dlg._commit_startup_defaults()
    assert app._startup_defaults["ticker"] == "NVDA"
    assert app.set_startup_default.call_count == len(STARTUP_DEFAULT_KEYS)
    assert app.ticker_var.get() == "AMD"


@pytest.mark.parametrize("worker,cap,expected_worker,expected_cap", [
    ("8", "0", 8, 1), ("bad", "99", 4, 20), ("4", "bad", 4, None),
])
def test_save_validates_numeric_inputs_and_commits_changed_settings(
    settings_dialog, worker, cap, expected_worker, expected_cap,
):
    env = settings_dialog
    dlg, app = env.dlg, env.app
    dlg._worker_var.set(worker)
    dlg._wl_cap_var.set(cap)
    dlg._tz_var.set("UTC")
    dlg._scroll_invert_var.set(True)
    dlg._snap_ohlc_var.set(True)
    dlg._sandbox_ref_var.set(" qqq ")
    dlg._skip_journal_var.set(True)
    dlg._splash_var.set(False)
    dlg._update_check_var.set(False)
    _SettingsDialog._on_ok(dlg)
    app.set_worker_count.assert_called_once_with(expected_worker)
    app.set_display_tz.assert_called_once_with("UTC")
    app.set_scroll_zoom_invert.assert_called_once_with(True)
    app.set_drawings_snap_to_ohlc.assert_called_once_with(True)
    assert env.prefs["sandbox_reference_symbol"] == "QQQ"
    assert env.prefs["sandbox_skip_detailed_journal"] is True
    assert env.prefs["splash_enabled"] is False
    assert env.prefs["update_check_on_startup"] is False
    assert env.prefs.get("watchlist_max_pinned") == expected_cap
    assert app.set_startup_default.call_count == len(STARTUP_DEFAULT_KEYS)
    dlg.destroy.assert_called_once()


def test_save_preserves_existing_worker_on_tcl_parse_error_and_skips_unchanged_fields(settings_dialog):
    env = settings_dialog
    env.dlg._worker_var = SimpleNamespace(get=Mock(side_effect=tk.TclError("invalid integer")))
    env.dlg._sandbox_ref_var.set(" ")
    _SettingsDialog._on_ok(env.dlg)
    env.app.set_worker_count.assert_called_once_with(4)
    env.app.set_display_tz.assert_not_called()
    env.app.set_scroll_zoom_invert.assert_not_called()
    env.app.set_drawings_snap_to_ohlc.assert_not_called()
    env.write.assert_not_called()
    env.dlg.destroy.assert_called_once()


def test_unsupported_scale_selection_does_not_change_live_fonts(settings_dialog):
    env = settings_dialog
    env.dlg._ui_scale_var.set("145%")
    _SettingsDialog._on_ui_scale_changed(env.dlg)
    env.app.set_ui_scale.assert_not_called()
    assert env.app._ui_scale == 1.0


@pytest.fixture
def watchlist_dialog(monkeypatch):
    monkeypatch.setattr(defaults, "get", lambda _key: 2)
    mgr = WatchlistManager()
    mgr.create("Momentum", ["AMD", "NVDA"])
    mgr.create("Index", ["SPY"])
    app = SimpleNamespace(_rebuild_watchlist_subtabs=Mock(), _on_menu_save_watchlists=Mock())
    dlg = SimpleNamespace(
        _parent_app=app, _mgr=mgr, _pin_dirty=False, destroy=Mock(),
        _selected_name=Mock(return_value="Momentum"), _refresh_names=Mock(),
        _on_select_name=Mock(),
    )
    dlg._on_close = MethodType(_WatchlistDialog._on_close, dlg)
    error = Mock()
    monkeypatch.setattr(dialogs.messagebox, "showerror", error)
    return SimpleNamespace(dlg=dlg, app=app, mgr=mgr, error=error)


def test_save_as_cancellation_keeps_unsaved_watchlist_dialog_open(watchlist_dialog):
    env = watchlist_dialog
    _WatchlistDialog._on_save_and_close(env.dlg)
    env.app._on_menu_save_watchlists.assert_called_once()
    assert env.mgr.loaded_path() is None
    assert env.mgr.is_dirty()
    env.dlg.destroy.assert_not_called()
    env.app._rebuild_watchlist_subtabs.assert_not_called()


@pytest.mark.parametrize("already_saved", [False, True])
def test_save_then_close_uses_parent_flow_and_rebuilds_pins_once(watchlist_dialog, tmp_path, already_saved):
    env = watchlist_dialog
    path = tmp_path / "watchlists.json"
    if already_saved:
        env.mgr.save_to_file(path)
    _WatchlistDialog._on_pin(env.dlg)
    env.app._rebuild_watchlist_subtabs.assert_not_called()
    env.app._on_menu_save_watchlists.side_effect = lambda: env.mgr.save_to_file(path)
    _WatchlistDialog._on_save_and_close(env.dlg)
    assert env.mgr.loaded_path() == path
    assert not env.mgr.is_dirty()
    env.app._on_menu_save_watchlists.assert_called_once()
    env.app._rebuild_watchlist_subtabs.assert_called_once()
    env.dlg.destroy.assert_called_once()


def test_save_failure_is_parented_and_leaves_dialog_open(watchlist_dialog):
    env = watchlist_dialog
    env.app._on_menu_save_watchlists.side_effect = OSError("disk full")
    _WatchlistDialog._on_save_and_close(env.dlg)
    env.error.assert_called_once()
    assert "disk full" in env.error.call_args.args[1]
    assert env.error.call_args.kwargs["parent"] is env.dlg
    env.dlg.destroy.assert_not_called()


@pytest.mark.parametrize("replacement", [None, "Momentum", "Index"])
def test_cancel_unchanged_or_duplicate_rename_preserves_manager(watchlist_dialog, monkeypatch, replacement):
    env = watchlist_dialog
    env.mgr.pin("Momentum")
    monkeypatch.setattr(dialogs, "_prompt_string", Mock(return_value=replacement))
    _WatchlistDialog._on_rename(env.dlg)
    assert env.mgr.list_names() == ["Momentum", "Index"]
    assert env.mgr.pinned_names() == ["Momentum"]
    assert not env.dlg._pin_dirty
    env.dlg._refresh_names.assert_not_called()
    if replacement == "Index":
        env.error.assert_called_once()
        assert env.error.call_args.kwargs["parent"] is env.dlg
    else:
        env.error.assert_not_called()


def test_pinned_rename_and_unpin_coalesce_subtab_rebuild_until_close(watchlist_dialog, monkeypatch):
    env = watchlist_dialog
    env.mgr.pin("Momentum")
    monkeypatch.setattr(dialogs, "_prompt_string", lambda *a, **k: "Leaders")
    _WatchlistDialog._on_rename(env.dlg)
    assert env.mgr.pinned_names() == ["Leaders"]
    assert env.mgr.get("Leaders").tickers == ["AMD", "NVDA"]
    env.dlg._selected_name.return_value = "Leaders"
    _WatchlistDialog._on_unpin(env.dlg)
    assert env.mgr.pinned_names() == []
    env.app._rebuild_watchlist_subtabs.assert_not_called()
    env.dlg._on_close()
    env.app._rebuild_watchlist_subtabs.assert_called_once()
    env.dlg.destroy.assert_called_once()


@pytest.mark.parametrize("confirmed", [True, False])
def test_delete_only_mutates_after_confirmation(watchlist_dialog, monkeypatch, confirmed):
    env = watchlist_dialog
    env.mgr.pin("Momentum")
    ask = Mock(return_value=confirmed)
    monkeypatch.setattr(dialogs.messagebox, "askyesno", ask)
    _WatchlistDialog._on_delete(env.dlg)
    assert (env.mgr.get("Momentum") is None) is confirmed
    assert env.dlg._pin_dirty is confirmed
    assert env.dlg._refresh_names.call_count == int(confirmed)
    assert env.mgr.get("Index").tickers == ["SPY"]
    assert ask.call_args.kwargs["parent"] is env.dlg


def test_pin_capacity_error_does_not_mark_dialog_dirty(watchlist_dialog):
    env = watchlist_dialog
    env.mgr.MAX_PINNED = 1
    env.mgr.pin("Index")
    _WatchlistDialog._on_pin(env.dlg)
    assert env.mgr.pinned_names() == ["Index"]
    assert not env.dlg._pin_dirty
    env.dlg._refresh_names.assert_not_called()
    env.error.assert_called_once()
    assert "Cannot pin more than 1" in env.error.call_args.args[1]


@pytest.mark.parametrize("action,picker,operation", [
    ("_on_import", "askopenfilename", "_import_watchlists_from_file"),
    ("_on_export", "asksaveasfilename", "save_to_file"),
])
def test_import_export_cancellation_performs_no_io(watchlist_dialog, monkeypatch, action, picker, operation):
    env = watchlist_dialog
    monkeypatch.setattr(dialogs.filedialog, picker, Mock(return_value=""))
    io = Mock()
    monkeypatch.setattr(dialogs if action == "_on_import" else env.mgr, operation, io)
    getattr(_WatchlistDialog, action)(env.dlg)
    io.assert_not_called()
    env.error.assert_not_called()
    assert env.mgr.list_names() == ["Momentum", "Index"]


@pytest.mark.parametrize("selected,at_cap,pin_enabled,unpin_enabled", [
    ("Momentum", False, True, False),
    ("Index", False, False, True),
    ("Momentum", True, False, False),
    (None, False, False, False),
])
def test_watchlist_refresh_preserves_selection_and_pin_button_contract(
    root, monkeypatch, tmp_path, selected, at_cap, pin_enabled, unpin_enabled,
):
    from tradinglab.gui import _modal_base, geometry_store

    geometry = geometry_store.GeometryStore(tmp_path / "geometry.json")
    monkeypatch.setattr(_modal_base, "_gstore", lambda: geometry)
    monkeypatch.setattr(defaults, "get", lambda _key: 1 if at_cap else 2)
    mgr = WatchlistManager()
    mgr.create("Momentum", ["AMD", "NVDA"])
    mgr.create("Index", ["SPY"])
    mgr.pin("Index")
    root._watchlists = mgr
    dlg = _WatchlistDialog(root)
    try:
        if selected:
            dlg._names.selection_set(selected)
            dlg._refresh_names()
            assert dlg._selected_name() == selected
            assert list(dlg._tickers.get(0, tk.END)) == mgr.get(selected).tickers
        else:
            dlg._names.selection_remove(*dlg._names.selection())
            dlg._on_select_name()
            assert dlg._tickers.size() == 0
        assert dlg._pin_btn.instate(["!disabled"]) is pin_enabled
        assert dlg._unpin_btn.instate(["!disabled"]) is unpin_enabled
    finally:
        dlg.destroy()
