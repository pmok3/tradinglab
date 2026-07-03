"""Tests for config ↔ indicator DECOUPLING (audit ``config-indicators-decoupled``).

Configuration files (File → Save / Load Configuration) are a layout / theme /
view snapshot and MUST NOT read or write the indicator manager:

* **Save** must not capture the indicator manager — there is no
  ``settings["indicators"]`` key and ``_capture_layout_into_settings`` never
  calls an indicator-capture hook.
* **Load** must not mutate the indicator manager — even a *legacy* config that
  still carries an ``indicators`` key is ignored, so loading it can never wipe
  the user's durable named-preset library.

Named presets persist independently via ``indicators.preset_store``; the active
indicator list is session-only (clean chart each launch). The end-to-end proof
through a real ``IndicatorManager`` is pinned by
``tests/smoke/test_smoke_full.py::check_d35c_config_indicator_decoupling``.
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


def _loaded_config_parent(manager, *, calls=None):
    """A duck-typed ChartApp stub exposing exactly what ``apply_loaded_config``
    touches, plus an ``_indicator_manager`` whose ``load_dict`` records calls."""
    return SimpleNamespace(
        _indicator_manager=manager,
        _display_tz="",
        _scroll_zoom_invert=False,
        _render=lambda: (calls.append("render") if calls is not None else None),
        replace_theme_overrides=lambda o: None,
        _apply_notebook_width_setting=lambda: None,
        _apply_persisted_view_settings=lambda: None,
        title=lambda *_a: None,
        ticker_var=None,
        interval_var=None,
        _watchlists=None,
    )


def test_chartapp_has_no_capture_indicators_setting() -> None:
    """The save-capture hook is removed: config no longer snapshots indicators."""
    import tradinglab.app as app_mod

    assert not hasattr(app_mod.ChartApp, "_capture_indicators_setting"), (
        "config is decoupled from indicators; _capture_indicators_setting "
        "must not exist"
    )


def test_indicators_is_not_a_persisted_settings_key() -> None:
    """No ``settings.set("indicators", …)`` literal remains in the source."""
    from tests._config_roundtrip_spec import persisted_settings_keys

    assert "indicators" not in persisted_settings_keys(), (
        "indicator state must not be written to the settings store"
    )


def test_capture_layout_does_not_capture_indicators() -> None:
    """``_capture_layout_into_settings`` must not invoke any indicator-capture
    hook, even when the parent happens to expose one."""
    from tradinglab.gui.config_manager import ConfigManager

    calls: list[str] = []
    parent = SimpleNamespace(
        _capture_notebook_width_setting=lambda: calls.append("width"),
        _capture_theme_setting=lambda: calls.append("theme"),
        # A stray hook (e.g. a legacy/monkeypatched build) must be ignored.
        _capture_indicators_setting=lambda: calls.append("indicators"),
    )
    ConfigManager._capture_layout_into_settings(parent)
    assert "width" in calls and "theme" in calls
    assert "indicators" not in calls, (
        "save must NOT capture indicator state into the config"
    )


def test_capture_layout_does_not_write_indicators_key() -> None:
    from tradinglab.gui.config_manager import ConfigManager

    _settings.clear()
    parent = SimpleNamespace(
        _capture_notebook_width_setting=lambda: None,
        _capture_theme_setting=lambda: None,
    )
    ConfigManager._capture_layout_into_settings(parent)
    assert _settings.get("indicators") is None


def test_apply_loaded_config_ignores_legacy_indicators_key() -> None:
    """A legacy config carrying an ``indicators`` dict must NOT reach the
    indicator manager — ``load_dict`` is never called, so presets survive."""
    _settings.clear()
    _settings.set("indicators", {
        "active_configs": [{"kind_id": "ema", "params": {"length": 9}}],
        "presets": {},          # the destructive case: empty presets
        "active_preset": None,
    })
    from tradinglab.gui.config_manager import ConfigManager

    root = SimpleNamespace(title=lambda *_a: None)
    cm = ConfigManager(root, intervals=("1d",), sources=["yfinance"])

    load_dict_calls: list = []
    manager = SimpleNamespace(load_dict=lambda d: load_dict_calls.append(d) or [])
    parent = _loaded_config_parent(manager)

    cm.apply_loaded_config(parent)

    assert load_dict_calls == [], (
        "apply_loaded_config must NOT call _indicator_manager.load_dict — "
        "loading a config must never mutate (or wipe) indicator state"
    )


def test_apply_loaded_config_ignores_indicators_when_absent() -> None:
    """No ``indicators`` key at all → manager still untouched (no crash)."""
    _settings.clear()
    from tradinglab.gui.config_manager import ConfigManager

    root = SimpleNamespace(title=lambda *_a: None)
    cm = ConfigManager(root, intervals=("1d",), sources=["yfinance"])

    load_dict_calls: list = []
    manager = SimpleNamespace(load_dict=lambda d: load_dict_calls.append(d) or [])
    render_calls: list[str] = []
    parent = _loaded_config_parent(manager, calls=render_calls)

    cm.apply_loaded_config(parent)

    assert load_dict_calls == []
    assert "render" in render_calls, "load should still repaint the chart"
