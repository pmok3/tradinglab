"""End-to-end: a light→dark theme toggle recolors the price-pane overlay
indicator names in place — WITHOUT a full re-render.

Reproduces the reported bug: after setting up an overlay indicator in light
mode and switching to dark mode, the indicator NAME on the price pane stayed
black until the user opened "Manage Indicators" (which forced a ``_render``).
The fix recolors the in-readout legend labels inside
``ThemeController._apply_overlay_artists`` (driven by ``app._apply_theme()``),
so the flip is immediate.

Uses the shared session ``app`` fixture; saves/restores dark mode + the
indicator manager so the rest of the smoke session is unaffected.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

from tests.smoke._helpers import _pump  # noqa: E402
from tradinglab.indicators.config import IndicatorConfig  # noqa: E402


def _find_row(app, config_id):
    box = app._readout_artists.get(app._ax_price)
    assert box is not None, "price axes must carry a readout offsetbox"
    for row in getattr(box, "_ind_rows", None) or ():
        if row.get("config_id") == config_id:
            return row
    return None


def test_dark_toggle_recolors_overlay_name_without_rerender(app):
    mgr = app._indicator_manager
    saved_dark = bool(app.dark_var.get())
    pre_ids = {c.id for c in mgr.list()}
    added_id = None
    try:
        # 1) Light mode + a visible SMA overlay on the price pane.
        app.dark_var.set(False)
        app._apply_theme()
        cfg = IndicatorConfig(kind_id="sma", scopes=("main",), params={"length": 3})
        mgr.add(cfg)
        added_id = cfg.id
        app._render()
        _pump(app, 0.05)

        light_text = app._theme_ctrl.theme["text"]
        row = _find_row(app, cfg.id)
        assert row is not None, "SMA overlay must appear as a readout legend row"
        label_ta = row.get("label_textarea")
        assert label_ta is not None, (
            "the real builder must stash the name TextArea so a theme swap can recolor it")
        assert label_ta._text.get_color() == light_text

        # 2) Flip to dark via the REAL toggle path (no _render afterwards).
        app.dark_var.set(True)
        app._apply_theme()
        _pump(app, 0.05)

        dark_text = app._theme_ctrl.theme["text"]
        assert dark_text != light_text, "light vs dark text colour must differ (sanity)"
        # The SAME label artist (no rebuild) must now read the dark text colour.
        assert label_ta._text.get_color() == dark_text, (
            "overlay indicator NAME must recolor on the dark toggle WITHOUT a re-render")
    finally:
        if added_id is not None:
            try:
                mgr.remove(added_id)
            except Exception:  # noqa: BLE001
                pass
        for c in list(mgr.list()):
            if c.id not in pre_ids:
                try:
                    mgr.remove(c.id)
                except Exception:  # noqa: BLE001
                    pass
        try:
            app.dark_var.set(saved_dark)
            app._apply_theme()
            app._render()
            _pump(app, 0.05)
        except Exception:  # noqa: BLE001
            pass
