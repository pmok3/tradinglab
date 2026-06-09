"""Live indicator updates spawn a new lower pane (e.g. RRVOL).

Pins the user-reported behavior: adding a non-overlay indicator that needs
its own pane must produce that pane on a live render — the path the Manage
Indicators dialog now uses by default (auto-apply ON), replacing the
deferred 'Apply' stopgap.
"""
from __future__ import annotations

import os

os.environ.setdefault("MPLBACKEND", "Agg")

import pytest


@pytest.fixture(scope="module")
def app():
    from tests.smoke._helpers import _pump, _stub_yfinance

    _stub_yfinance()
    from tradinglab.app import ChartApp

    a = ChartApp()
    try:
        a.geometry("1000x680-3000-3000")
    except Exception:
        pass
    try:
        a.withdraw()
    except Exception:
        pass
    _pump(a, 0.3)
    try:
        a._render()
    except Exception:
        pass
    _pump(a, 0.2)
    yield a
    try:
        a._on_close()
    except Exception:
        pass


def _pane_count(app, slot="primary"):
    ps = app._panel_state.get(slot, {})
    return len(ps.get("ind_axes", []) or [])


def test_live_add_rrvol_spawns_pane(app):
    from tests.smoke._helpers import _pump
    from tradinglab.indicators.config import IndicatorConfig

    interval = app.interval_var.get()
    mgr = app._indicator_manager
    base = _pane_count(app)

    cfg = IndicatorConfig(kind_id="rrvol", intervals=(interval,))
    added = mgr.add(cfg)  # fires the manager subscriber → live render
    try:
        # Live render path is async (after_idle); pump to let it land.
        _pump(app, 0.3)
        assert _pane_count(app) == base + 1, (
            "adding a pane-requiring indicator (RRVOL) must spawn one new "
            "lower pane on a live render"
        )
    finally:
        mgr.remove(added.id)
        _pump(app, 0.3)

    # Removing it tears the pane back down (live).
    assert _pane_count(app) == base


def test_live_remove_pane_indicator_tears_down_pane(app):
    from tests.smoke._helpers import _pump
    from tradinglab.indicators.config import IndicatorConfig

    interval = app.interval_var.get()
    mgr = app._indicator_manager
    base = _pane_count(app)
    a = mgr.add(IndicatorConfig(kind_id="rsi", intervals=(interval,)))
    _pump(app, 0.3)
    assert _pane_count(app) == base + 1
    mgr.remove(a.id)
    _pump(app, 0.3)
    assert _pane_count(app) == base
