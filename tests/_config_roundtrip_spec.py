"""Shared spec for the config save/load round-trip meta-test.

Single source of truth for *which* persisted settings keys must survive a
File → Save/Load Configuration round-trip (restored live, no relaunch) vs.
which are intentionally next-launch / out of scope. Imported by:

* ``tests/unit/gui/test_config_roundtrip_meta.py`` — the drift guard: every
  directly-persisted key (a ``settings.set("KEY", …)`` literal in the source)
  must be classified here, so a NEW persisted setting fails the build until
  it is either wired into the load-restore path (ROUNDTRIP) or explicitly
  documented as next-launch (KNOWN_NON_ROUNDTRIP).
* ``tests/smoke/test_smoke_full.py::check_d35b_view_settings_round_trip`` —
  the behavioral check: drives each ROUNDTRIP key through a real ChartApp
  (set → save → reset → load → assert restored).

See ``app.spec.md`` → "Persisted view-settings round-trip" and §
``config-roundtrip-meta``.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "tradinglab"

# Keys persisted via a direct ``settings.set("KEY", …)`` that ARE restored
# live by ``ConfigManager.apply_loaded_config`` (directly or via
# ``ChartApp._apply_persisted_view_settings`` / ``_apply_theme``). Indicator
# state is intentionally NOT a config key: configuration files are decoupled
# from the indicator manager (audit ``config-indicators-decoupled``), so
# ``indicators`` is no longer persisted here — named presets persist on their
# own via ``indicators.preset_store`` and the active list is session-only.
ROUNDTRIP_KEYS: frozenset[str] = frozenset({
    # Pre-existing (covered by check_d35a / d14 / notebook-width tests):
    "display_tz",
    "scroll_zoom_invert",
    "theme_overrides",
    "startup_defaults",          # composite; theme sub-key applied live too
    "layout.notebook_width_px",
    # Wired into _apply_persisted_view_settings (audit config-roundtrip-meta):
    "heikin_ashi",
    "highlight_key_bars",
    "highlight_ha_flat",
    "volume_tod_enabled",
    "use_colorblind_palette",
    "drawings_snap_to_ohlc",
    "chartstack.enabled",
    "ui_scale",
    "worker_count",
    "ratio_rebase",
})

# The behavioral smoke check exercises exactly these (the live view/behavior
# toggles wired into ``_apply_persisted_view_settings``). The pre-existing
# keys above are already pinned by their own tests, so the smoke registry is
# asserted to equal this set (keeps the two in lockstep).
ROUNDTRIP_BEHAVIORAL_KEYS: frozenset[str] = frozenset({
    "heikin_ashi",
    "highlight_key_bars",
    "highlight_ha_flat",
    "volume_tod_enabled",
    "use_colorblind_palette",
    "drawings_snap_to_ohlc",
    "chartstack.enabled",
    "ui_scale",
    "worker_count",
    "ratio_rebase",
})

# Persisted keys intentionally NOT restored live on config load, each with a
# reason. A new persisted key must land in ROUNDTRIP_KEYS or here.
KNOWN_NON_ROUNDTRIP: dict[str, str] = {
    "chartstack.fixed_preset_symbols": (
        "ChartStack symbol binding — read live by gui.chartstack."
        "settings_adapter when the stack (re)builds; no dedicated re-bind on "
        "load. Takes effect when the pane is shown/refreshed."
    ),
    "chartstack.binding.mode": (
        "ChartStack binding mode — same as fixed_preset_symbols; consumed "
        "live by the settings_adapter at (re)build time."
    ),
    "local_data": (
        "BYOD local-data source registration is a startup/dialog concern; "
        "re-registering data sources mid-session on config load is out of "
        "scope (would need to rebuild the source registry + combobox)."
    ),
}


def persisted_settings_keys() -> set[str]:
    """Scan ``src/tradinglab`` for every ``settings.set("KEY", …)`` literal.

    Matches calls of the form ``settings.set(...)`` / ``_settings.set(...)``
    whose receiver is a bare name ending in ``settings`` and whose first
    argument is a string constant. This is the canonical "what gets written
    to settings.json" surface for the drift guard.
    """
    keys: set[str] = set()
    for py in _SRC_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "set":
                continue
            recv = func.value
            if not isinstance(recv, ast.Name) or not recv.id.endswith("settings"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
    return keys
