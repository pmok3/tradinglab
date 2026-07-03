"""Tests for the user-configurable saved watchlist (notebook) width.

Audit ``watchlist-width-setting``: the user drags the chart|watchlist
divider to a preferred width; File → Save Configuration captures it
into ``settings["layout.notebook_width_px"]``; File → Load
Configuration restores it. These exercise the three ``ChartApp``
helpers in isolation (bound to a stub ``self``, no Tk required) plus
the ConfigManager save/load wiring.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab import settings as _settings

_KEY = "layout.notebook_width_px"


@pytest.fixture(autouse=True)
def _isolate_settings():
    snap = _settings.load()
    yield
    _settings.save(snap)


def _make_app_stub(**attrs):
    """Build a stub ``self`` with the real ChartApp notebook-width
    helper methods bound, so intra-method ``self._other_helper(...)``
    calls resolve. Pass instance attributes (``_chartstack``,
    ``_main_paned``, ...) as kwargs."""
    import tradinglab.app as app_mod
    stub = SimpleNamespace(**attrs)
    for name in (
        "_current_notebook_width",
        "_capture_notebook_width_setting",
        "_apply_notebook_width_setting",
        "_chartstack_currently_visible",
        "_capture_notebook_boundary",
    ):
        setattr(stub, name, getattr(app_mod.ChartApp, name).__get__(stub))
    return stub


# ---------------------------------------------------------------------------
# _current_notebook_width
# ---------------------------------------------------------------------------


def _fake_paned(*, width: int, sash_positions: dict[int, int], panes: list[str]):
    return SimpleNamespace(
        winfo_width=lambda: width,
        sashpos=lambda i: sash_positions[i],
        panes=lambda: panes,
    )


def test_current_notebook_width_2pane() -> None:
    # 2-pane: chart|notebook sash (index 0) at x=1186; width 1920 → nb=734.
    paned = _fake_paned(width=1920, sash_positions={0: 1186}, panes=["chart", "nb"])
    stub = _make_app_stub(_chartstack=None, _main_paned=paned)
    assert stub._current_notebook_width() == 1920 - 1186


def test_current_notebook_width_3pane() -> None:
    cs = SimpleNamespace()
    # 3-pane: chart|notebook sash is index 1 at x=1406 (CS=220 + chart=1186).
    paned = _fake_paned(
        width=1920, sash_positions={0: 220, 1: 1406},
        panes=[str(cs), "chart", "nb"],
    )
    stub = _make_app_stub(_chartstack=cs, _main_paned=paned)
    assert stub._current_notebook_width() == 1920 - 1406


def test_current_notebook_width_returns_zero_when_unreadable() -> None:
    stub = _make_app_stub(_chartstack=None, _main_paned=None)
    assert stub._current_notebook_width() == 0


# ---------------------------------------------------------------------------
# _capture_notebook_width_setting
# ---------------------------------------------------------------------------


def test_capture_writes_current_width_to_settings() -> None:
    _settings.clear()
    paned = _fake_paned(width=1920, sash_positions={0: 1200}, panes=["chart", "nb"])
    stub = _make_app_stub(_chartstack=None, _main_paned=paned)
    stub._capture_notebook_width_setting()
    assert _settings.get(_KEY) == 720  # 1920 - 1200


def test_capture_skips_when_width_unreadable() -> None:
    """If the width can't be measured (0), don't clobber settings with
    a bogus value."""
    _settings.clear()
    _settings.set(_KEY, 500)
    stub = _make_app_stub(_chartstack=None, _main_paned=None)
    stub._capture_notebook_width_setting()
    # Existing value untouched.
    assert _settings.get(_KEY) == 500


# ---------------------------------------------------------------------------
# _apply_notebook_width_setting
# ---------------------------------------------------------------------------


def _with_capture_apply(stub) -> dict:
    box: dict = {}
    stub._apply_forced_sash = (
        lambda paned, positions, **kw: box.update(positions=list(positions))
    )
    return box


def test_apply_forces_sash_to_saved_width() -> None:
    from tradinglab.constants import compute_main_paned_sashes
    _settings.clear()
    _settings.set(_KEY, 600)
    paned = _fake_paned(width=1920, sash_positions={0: 1186}, panes=["chart", "nb"])
    stub = _make_app_stub(_chartstack=None, _main_paned=paned)
    box = _with_capture_apply(stub)
    stub._apply_notebook_width_setting()
    expected = compute_main_paned_sashes(
        1920, chartstack_visible=False, notebook_width_px=600)
    assert box["positions"] == expected
    assert 1920 - box["positions"][-1] == 600


def test_apply_noop_when_setting_absent() -> None:
    _settings.clear()
    paned = _fake_paned(width=1920, sash_positions={0: 1186}, panes=["chart", "nb"])
    stub = _make_app_stub(_chartstack=None, _main_paned=paned)
    box = _with_capture_apply(stub)
    stub._apply_notebook_width_setting()
    assert "positions" not in box  # _apply_forced_sash never called


def test_apply_noop_when_setting_garbage() -> None:
    _settings.clear()
    _settings.set(_KEY, "not-an-int")
    paned = _fake_paned(width=1920, sash_positions={0: 1186}, panes=["chart", "nb"])
    stub = _make_app_stub(_chartstack=None, _main_paned=paned)
    box = _with_capture_apply(stub)
    stub._apply_notebook_width_setting()
    assert "positions" not in box


def test_apply_honours_chartstack_visible() -> None:
    from tradinglab.constants import compute_main_paned_sashes
    _settings.clear()
    _settings.set(_KEY, 600)
    cs = SimpleNamespace()
    paned = _fake_paned(
        width=1920, sash_positions={0: 220, 1: 1320},
        panes=[str(cs), "chart", "nb"],
    )
    stub = _make_app_stub(_chartstack=cs, _main_paned=paned)
    box = _with_capture_apply(stub)
    stub._apply_notebook_width_setting()
    expected = compute_main_paned_sashes(
        1920, chartstack_visible=True, notebook_width_px=600)
    assert box["positions"] == expected
    assert box["positions"][0] == 220  # CS column preserved
    assert 1920 - box["positions"][1] == 600  # notebook width applied


# ---------------------------------------------------------------------------
# ConfigManager wiring — capture on save, apply on load
# ---------------------------------------------------------------------------
#
# These use a STUB root (SimpleNamespace), NOT a real ``tk.Tk()``:
# ConfigManager only touches ``root`` as a title fallback, and both
# methods under test take an explicit ``parent``. Avoiding a second
# Tcl interpreter sidesteps the §7.5 ``Tcl_AsyncDelete`` crash.


def _config_manager_with_stub_root():
    from tradinglab.gui.config_manager import ConfigManager
    root = SimpleNamespace(title=lambda *_a: None)
    return ConfigManager(root, intervals=("1d",), sources=["yfinance"])


def test_config_manager_save_captures_width(monkeypatch, tmp_path) -> None:
    """``save_config_as`` must call the parent's
    ``_capture_notebook_width_setting`` before exporting so the
    current divider position lands in the saved file."""
    _settings.clear()
    cfg = _config_manager_with_stub_root()
    calls: list[str] = []
    cfg_path = tmp_path / "cfg.json"
    monkeypatch.setattr(
        "tradinglab.gui.config_manager.filedialog.asksaveasfilename",
        lambda **kw: str(cfg_path),
    )
    monkeypatch.setattr(cfg, "push_recent", lambda *a, **k: None)
    parent = SimpleNamespace(
        _capture_notebook_width_setting=lambda: calls.append("captured"),
        title=lambda *_a: None,
        ticker_var=None,
        interval_var=None,
        _watchlists=None,
    )
    cfg.save_config_as(parent)
    assert "captured" in calls, (
        "save_config_as must call parent._capture_notebook_width_setting "
        "before exporting"
    )
    assert cfg_path.exists()


def test_config_manager_apply_loaded_applies_width() -> None:
    """``apply_loaded_config`` must call the parent's
    ``_apply_notebook_width_setting`` so a loaded width takes effect on
    the live sash."""
    _settings.clear()
    cfg = _config_manager_with_stub_root()
    calls: list[str] = []
    parent = SimpleNamespace(
        _apply_notebook_width_setting=lambda: calls.append("applied"),
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
    cfg.apply_loaded_config(parent)
    assert "applied" in calls, (
        "apply_loaded_config must call parent._apply_notebook_width_setting"
    )
