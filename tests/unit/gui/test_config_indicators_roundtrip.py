"""Tests for the indicator (active list + presets) config round-trip.

Audit ``config-indicators-roundtrip``: the IndicatorManager state lives only
in memory, so File → Save Configuration must capture it via
``ChartApp._capture_indicators_setting`` (mirroring `_capture_theme_setting`)
for File → Load Configuration to restore it (`apply_loaded_config` already
calls `_indicator_manager.load_dict`). These exercise the capture helper +
the ConfigManager wiring with stubs (no Tk); the end-to-end round-trip
through a real IndicatorManager is pinned by
``tests/smoke/test_smoke_full.py::check_d35c_indicator_presets_round_trip``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab import settings as _settings


@pytest.fixture(autouse=True)
def _isolate_settings():
    snap = _settings.load()
    yield
    _settings.save(snap)


def _capture_stub(manager):
    import tradinglab.app as app_mod

    stub = SimpleNamespace(_indicator_manager=manager)
    stub._capture_indicators_setting = (
        app_mod.ChartApp._capture_indicators_setting.__get__(stub)
    )
    return stub


def test_capture_indicators_writes_manager_to_dict() -> None:
    _settings.clear()
    payload = {
        "active_configs": [{"kind_id": "ema", "params": {"length": 9}}],
        "presets": {"p": []},
        "active_preset": "p",
    }
    stub = _capture_stub(SimpleNamespace(to_dict=lambda: payload))
    stub._capture_indicators_setting()
    assert _settings.get("indicators") == payload


def test_capture_indicators_noop_without_manager() -> None:
    _settings.clear()
    stub = _capture_stub(None)
    stub._capture_indicators_setting()  # must not raise
    assert _settings.get("indicators") is None


def test_capture_indicators_swallows_to_dict_error() -> None:
    _settings.clear()

    def _boom():
        raise RuntimeError("manager exploded")

    stub = _capture_stub(SimpleNamespace(to_dict=_boom))
    stub._capture_indicators_setting()  # must not raise
    assert _settings.get("indicators") is None


def test_capture_layout_invokes_indicator_capture() -> None:
    from tradinglab.gui.config_manager import ConfigManager

    calls: list[str] = []
    parent = SimpleNamespace(
        _capture_notebook_width_setting=lambda: None,
        _capture_theme_setting=lambda: None,
        _capture_indicators_setting=lambda: calls.append("indicators"),
    )
    ConfigManager._capture_layout_into_settings(parent)
    assert "indicators" in calls, (
        "save must call parent._capture_indicators_setting before exporting"
    )


def test_capture_layout_tolerates_missing_indicator_hook() -> None:
    from tradinglab.gui.config_manager import ConfigManager

    parent = SimpleNamespace(
        _capture_notebook_width_setting=lambda: None,
        _capture_theme_setting=lambda: None,
    )
    # Must not raise when the indicator hook is absent (older stub).
    ConfigManager._capture_layout_into_settings(parent)


def test_apply_loaded_config_restores_indicators() -> None:
    _settings.clear()
    payload = {
        "active_configs": [{"kind_id": "ema", "params": {"length": 9}}],
        "presets": {"mine": []},
        "active_preset": "mine",
    }
    _settings.set("indicators", payload)
    from tradinglab.gui.config_manager import ConfigManager

    root = SimpleNamespace(title=lambda *_a: None)
    cm = ConfigManager(root, intervals=("1d",), sources=["yfinance"])
    loaded: list = []
    parent = SimpleNamespace(
        _indicator_manager=SimpleNamespace(
            load_dict=lambda d: (loaded.append(d) or [])),
        _display_tz="",
        _scroll_zoom_invert=False,
        _render=lambda: None,
        _refill_table=lambda: None,
        replace_theme_overrides=lambda o: None,
        _apply_notebook_width_setting=lambda: None,
        _apply_persisted_view_settings=lambda: None,
        title=lambda *_a: None,
        ticker_var=None,
        interval_var=None,
        _watchlists=None,
    )
    cm.apply_loaded_config(parent)
    assert loaded and loaded[0] == payload, (
        "apply_loaded_config must pass the loaded indicators dict to "
        "_indicator_manager.load_dict"
    )
