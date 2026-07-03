"""Tests for the light/dark theme config round-trip.

Audit ``config-theme-roundtrip``: the base light/dark theme must survive
File → Save Configuration → File → Load Configuration, just like the
timezone, scroll-zoom direction, theme colour overrides, and watchlist
width already do. The persisted home for the base theme is
``settings['startup_defaults']['theme']``.

These exercise the ``ChartApp._capture_theme_setting`` helper (bound to a
stub ``self``, no Tk required) plus the ConfigManager save-capture /
load-apply wiring, mirroring ``test_notebook_width_setting.py``. Using a
``SimpleNamespace`` stub instead of a real ``tk.Tk()`` sidesteps the §7.5
``Tcl_AsyncDelete`` teardown crash.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab import settings as _settings
from tradinglab.constants import BUILTIN_STARTUP_DEFAULTS


@pytest.fixture(autouse=True)
def _isolate_settings():
    snap = _settings.load()
    yield
    _settings.save(snap)


def _config_manager_with_stub_root():
    from tradinglab.gui.config_manager import ConfigManager

    root = SimpleNamespace(title=lambda *_a: None)
    return ConfigManager(root, intervals=("1d",), sources=["yfinance"])


def _theme_capture_stub(*, dark: bool, set_startup_default):
    """Bind the real ``ChartApp._capture_theme_setting`` to a stub self."""
    import tradinglab.app as app_mod

    stub = SimpleNamespace(
        dark_var=SimpleNamespace(get=lambda: dark),
        set_startup_default=set_startup_default,
    )
    stub._capture_theme_setting = (
        app_mod.ChartApp._capture_theme_setting.__get__(stub)
    )
    return stub


# ---------------------------------------------------------------------------
# ChartApp._capture_theme_setting — save side
# ---------------------------------------------------------------------------


def test_capture_theme_persists_dark() -> None:
    """A dark session writes ``startup_defaults.theme == 'dark'`` so the
    saved config carries it."""
    _settings.clear()
    cfg = _config_manager_with_stub_root()
    stub = _theme_capture_stub(dark=True, set_startup_default=cfg.set_startup_default)
    stub._capture_theme_setting()
    assert _settings.get("startup_defaults", {}).get("theme") == "dark"


def test_capture_theme_light_is_sparse() -> None:
    """A light session equals the builtin, so the sparse-save omits the
    key (loading the absence resolves back to the builtin light)."""
    assert BUILTIN_STARTUP_DEFAULTS["theme"] == "light"
    _settings.clear()
    cfg = _config_manager_with_stub_root()
    stub = _theme_capture_stub(dark=False, set_startup_default=cfg.set_startup_default)
    stub._capture_theme_setting()
    assert "theme" not in _settings.get("startup_defaults", {})


def test_capture_theme_dark_then_light_removes_key() -> None:
    """Toggling back to light after a dark capture clears the persisted
    override — the round-trip works in both directions."""
    _settings.clear()
    cfg = _config_manager_with_stub_root()
    _theme_capture_stub(
        dark=True, set_startup_default=cfg.set_startup_default
    )._capture_theme_setting()
    assert _settings.get("startup_defaults", {}).get("theme") == "dark"
    _theme_capture_stub(
        dark=False, set_startup_default=cfg.set_startup_default
    )._capture_theme_setting()
    assert "theme" not in _settings.get("startup_defaults", {})


def test_capture_theme_unreadable_var_is_noop() -> None:
    """A dark_var that raises on ``.get()`` must not write anything."""
    _settings.clear()
    calls: list = []

    def _boom():
        raise RuntimeError("no tk")

    import tradinglab.app as app_mod

    stub = SimpleNamespace(
        dark_var=SimpleNamespace(get=_boom),
        set_startup_default=lambda *a, **k: calls.append(a),
    )
    stub._capture_theme_setting = (
        app_mod.ChartApp._capture_theme_setting.__get__(stub)
    )
    stub._capture_theme_setting()
    assert calls == []


# ---------------------------------------------------------------------------
# ConfigManager._capture_layout_into_settings — invokes theme capture
# ---------------------------------------------------------------------------


def test_capture_layout_invokes_theme_capture() -> None:
    from tradinglab.gui.config_manager import ConfigManager

    calls: list[str] = []
    parent = SimpleNamespace(
        _capture_notebook_width_setting=lambda: calls.append("width"),
        _capture_theme_setting=lambda: calls.append("theme"),
    )
    ConfigManager._capture_layout_into_settings(parent)
    assert "theme" in calls, (
        "save must call parent._capture_theme_setting before exporting"
    )


def test_capture_layout_tolerates_missing_theme_hook() -> None:
    """A parent lacking the theme hook (older stub) is a silent no-op."""
    from tradinglab.gui.config_manager import ConfigManager

    parent = SimpleNamespace(
        _capture_notebook_width_setting=lambda: None,
    )
    # Must not raise.
    ConfigManager._capture_layout_into_settings(parent)


# ---------------------------------------------------------------------------
# ConfigManager.apply_loaded_config — load side applies the theme live
# ---------------------------------------------------------------------------


def _apply_loaded_parent(dark_initial: bool):
    box = {"dark": dark_initial, "applied": 0}
    parent = SimpleNamespace(
        dark_var=SimpleNamespace(
            get=lambda: box["dark"],
            set=lambda v: box.__setitem__("dark", bool(v)),
        ),
        _apply_theme=lambda: box.__setitem__("applied", box["applied"] + 1),
        _apply_notebook_width_setting=lambda: None,
        _display_tz="",
        _scroll_zoom_invert=False,
        _indicator_manager=SimpleNamespace(load_dict=lambda d: []),
        _render=lambda: None,
        replace_theme_overrides=lambda o: None,
        title=lambda *_a: None,
        ticker_var=None,
        interval_var=None,
        _watchlists=None,
    )
    return parent, box


def test_apply_loaded_config_enters_dark() -> None:
    """Loading a config whose startup_defaults.theme is dark flips the
    live ``dark_var`` on and cascades ``_apply_theme``."""
    _settings.clear()
    _settings.set("startup_defaults", {"theme": "dark"})
    cfg = _config_manager_with_stub_root()
    parent, box = _apply_loaded_parent(dark_initial=False)
    cfg.apply_loaded_config(parent)
    assert box["dark"] is True, "loaded dark theme must be applied to dark_var"
    assert box["applied"] >= 1, "apply_loaded_config must cascade _apply_theme"


def test_apply_loaded_config_resets_to_light() -> None:
    """Loading a light config (no theme override → builtin light) while
    the live app is dark resets it back to light."""
    _settings.clear()
    _settings.set("startup_defaults", {})
    cfg = _config_manager_with_stub_root()
    parent, box = _apply_loaded_parent(dark_initial=True)
    cfg.apply_loaded_config(parent)
    assert box["dark"] is False, "loaded light theme must reset dark_var"
    assert box["applied"] >= 1


def test_apply_loaded_config_without_dark_var_is_noop() -> None:
    """A parent that has no ``dark_var`` (headless stub) must not raise."""
    _settings.clear()
    _settings.set("startup_defaults", {"theme": "dark"})
    cfg = _config_manager_with_stub_root()
    parent = SimpleNamespace(
        _apply_notebook_width_setting=lambda: None,
        _display_tz="",
        _scroll_zoom_invert=False,
        _indicator_manager=SimpleNamespace(load_dict=lambda d: []),
        _render=lambda: None,
        replace_theme_overrides=lambda o: None,
        title=lambda *_a: None,
        ticker_var=None,
        interval_var=None,
        _watchlists=None,
    )
    # Must not raise even though dark_var / _apply_theme are absent.
    cfg.apply_loaded_config(parent)
