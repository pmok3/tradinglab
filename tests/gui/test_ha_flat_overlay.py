"""Tests for the View → Highlight Flat HA Candles overlay infrastructure.

Three layers are exercised:

1. :func:`tradinglab.rendering.brighter_shade` and
   :func:`tradinglab.rendering.darker_shade` — pure colour-derivation
   helpers. Theme-aware (separate dark vs light mode behaviour).
2. :func:`tradinglab.rendering.draw_candlesticks` — the
   ``flat_overlay`` integration: a hatched ``PolyCollection`` is layered
   on top of the normal bull/bear body for indexed bars, the body fill
   stays untouched, and hollow (key bar) takes priority when both
   overlays cover the same bar.
3. ``ChartApp._ha_flat_overlay_for`` — the bridge from the View toggle +
   HA-display state to the renderer's ``flat_overlay`` dict. Tested via
   a minimal mock that exposes only the attributes the helper reads, so
   we don't have to instantiate Tk.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from typing import List

import numpy as np
import pytest

import matplotlib

matplotlib.use("Agg")  # headless — must precede pyplot
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import to_rgba  # noqa: E402

from tradinglab.constants import BEAR_COLOR, BULL_COLOR  # noqa: E402
from tradinglab.models import Candle  # noqa: E402
from tradinglab.rendering import (  # noqa: E402
    brighter_shade,
    darker_shade,
    draw_candlesticks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle(o, h, l_, c, *, t=None, session="regular") -> Candle:
    return Candle(
        date=t or _dt.datetime(2024, 1, 2, 9, 30),
        open=o, high=h, low=l_, close=c,
        volume=1000, session=session,
    )


def _uptrend_candles(n: int = 6) -> List[Candle]:
    """Strong uptrend → most bars qualify as bull-flat-bottom under HA."""
    out: List[Candle] = []
    t0 = _dt.datetime(2024, 1, 2, 9, 30)
    for i in range(n):
        o = 100.0 + i
        c = o + 1.0
        out.append(_candle(o, c + 0.5, o - 0.5, c,
                           t=t0 + _dt.timedelta(minutes=i)))
    return out


def _make_app_mock(
    *, ha_on: bool, flat_on: bool, dark_mode: bool,
):
    """Minimal stand-in that satisfies ``_ha_flat_overlay_for``.

    The helper reads only ``self._highlight_ha_flat_var.get()``,
    ``self._ha_display_var.get()``, and ``self.dark_var.get()``. We
    bind the bound method to a SimpleNamespace via descriptor magic so
    we don't have to subclass ChartApp (which would require Tk).
    """
    ns = SimpleNamespace(
        _highlight_ha_flat_var=SimpleNamespace(get=lambda: flat_on),
        _ha_display_var=SimpleNamespace(get=lambda: ha_on),
        dark_var=SimpleNamespace(get=lambda: dark_mode),
    )
    # Pull the unbound function off the class and bind it manually.
    from tradinglab.app import ChartApp
    ns._ha_flat_overlay_for = (
        ChartApp._ha_flat_overlay_for.__get__(ns)
    )
    return ns


# ---------------------------------------------------------------------------
# 1. brighter_shade / darker_shade — pure colour helpers
# ---------------------------------------------------------------------------


def test_brighter_shade_preserves_alpha():
    rgba = (0.5, 0.6, 0.7, 0.42)
    out = brighter_shade(rgba, dark_mode=False)
    assert out[3] == pytest.approx(0.42)
    out_dark = brighter_shade(rgba, dark_mode=True)
    assert out_dark[3] == pytest.approx(0.42)


def test_brighter_shade_pushes_saturation_to_max():
    """A muted hue must come back fully saturated (s == 1.0 in HLS)."""
    import colorsys
    base = (0.5, 0.55, 0.5, 1.0)  # near-grey green
    out = brighter_shade(base, dark_mode=False)
    h, l, s = colorsys.rgb_to_hls(out[0], out[1], out[2])
    assert s == pytest.approx(1.0, abs=1e-6)


def test_brighter_shade_dark_mode_lifts_lightness():
    """In dark mode the helper clamps lightness up so it reads on dark bg."""
    import colorsys
    # Start with a low-lightness colour.
    base = to_rgba("#1a3a36")  # dark teal — lightness ≈ 0.16
    out = brighter_shade(base, dark_mode=True)
    _h, l_out, _s = colorsys.rgb_to_hls(out[0], out[1], out[2])
    assert l_out >= 0.55 - 1e-6


def test_brighter_shade_light_mode_clamps_lightness_to_vivid_band():
    """Light mode keeps lightness in the [0.40, 0.55] band."""
    import colorsys
    # Very light pastel input.
    base = to_rgba("#ffe5e0")  # near-white pinkish — lightness ≈ 0.94
    out = brighter_shade(base, dark_mode=False)
    _h, l_out, _s = colorsys.rgb_to_hls(out[0], out[1], out[2])
    assert 0.40 - 1e-6 <= l_out <= 0.55 + 1e-6


def test_brighter_shade_bull_bear_distinct_per_theme():
    """Bull and bear accents must never collapse to the same colour."""
    bull = to_rgba(BULL_COLOR)
    bear = to_rgba(BEAR_COLOR)
    for dark in (False, True):
        b = brighter_shade(bull, dark_mode=dark)
        r = brighter_shade(bear, dark_mode=dark)
        assert b != r


def test_darker_shade_bull_bear_distinct_per_theme():
    """Bull and bear hatch colours via darker_shade stay distinct."""
    bull = to_rgba(BULL_COLOR)
    bear = to_rgba(BEAR_COLOR)
    for dark in (False, True):
        b = darker_shade(bull, dark_mode=dark)
        r = darker_shade(bear, dark_mode=dark)
        assert b != r


# ---------------------------------------------------------------------------
# 2. draw_candlesticks — flat_overlay integration
# ---------------------------------------------------------------------------


def _make_overlay(*, bull_indices=(), bear_indices=()) -> dict:
    """Build a minimal flat_overlay dict using vivid hatch colours."""
    return {
        "bull_indices": frozenset(int(i) for i in bull_indices),
        "bear_indices": frozenset(int(i) for i in bear_indices),
        "bull_color": (0.0, 0.0, 0.0, 1.0),  # black — easy to verify
        "bear_color": (1.0, 1.0, 1.0, 1.0),  # white
        "bull_hatch": "xxx",
        "bear_hatch": "xxx",
    }


def test_draw_candlesticks_applies_flat_hatch_overlay():
    """A bar in ``flat_overlay['bull_indices']`` keeps its normal body
    face colour AND gains a hatched overlay PolyCollection on top.
    The hatch collection has a transparent face, the configured
    edgecolor as the hatch line colour, and the configured hatch
    pattern."""
    candles = _uptrend_candles(5)
    fig, ax = plt.subplots()
    try:
        overlay = _make_overlay(bull_indices=[2])
        wicks, bodies = draw_candlesticks(
            ax, candles, start=0, end=5, flat_overlay=overlay
        )
        assert bodies is not None

        # Underlying body face for indexed bar is UNCHANGED — still
        # the bull bull/bear hue.
        bull_rgba = to_rgba(BULL_COLOR, 1.0)
        face_colors = bodies.get_facecolors()
        np.testing.assert_allclose(face_colors[2], bull_rgba)
        # All bars in this fixture are bull, so every face is bull_rgba.
        for i in range(5):
            np.testing.assert_allclose(face_colors[i], bull_rgba)

        # One overlay collection added (bull only — no bear-flat in this
        # uptrend fixture).
        hatch_cols = getattr(bodies, "_sc_flat_hatch_collections", [])
        assert len(hatch_cols) == 1
        hc = hatch_cols[0]
        # Hatch pattern is what we asked for.
        assert hc.get_hatch() == "xxx"
        # Face is transparent (lets the underlying body show through gaps).
        hc_faces = hc.get_facecolors()
        assert hc_faces.shape[0] >= 1
        for row in hc_faces:
            assert row[3] == pytest.approx(0.0)
        # Edgecolor (= hatch line colour) is the configured bull colour.
        hc_edges = hc.get_edgecolors()
        np.testing.assert_allclose(hc_edges[0], (0.0, 0.0, 0.0, 1.0))
        # Geometry: the overlay has exactly one polygon — the bull-flat bar.
        assert len(hc.get_paths()) == 1
        # The accent-mode tag is set so the H1 fastpath can bail.
        assert getattr(bodies, "_sc_accent_mode", False) is True
        # The overlay artist is on the axes.
        assert hc in list(ax.collections)
    finally:
        plt.close(fig)


def test_draw_candlesticks_no_overlay_is_legacy_path():
    """Omitting ``flat_overlay`` keeps the all-solid behavior and adds
    no hatch artists."""
    candles = _uptrend_candles(3)
    fig, ax = plt.subplots()
    try:
        wicks, bodies = draw_candlesticks(ax, candles, start=0, end=3)
        assert bodies is not None
        assert getattr(bodies, "_sc_accent_mode", False) is False
        assert getattr(bodies, "_sc_flat_hatch_collections", []) == []
        # All bars use the bull body colour for both face AND edge.
        bull_rgba = to_rgba(BULL_COLOR, 1.0)
        for i in range(3):
            np.testing.assert_allclose(bodies.get_facecolors()[i], bull_rgba)
    finally:
        plt.close(fig)


def test_draw_candlesticks_hollow_takes_priority_over_flat():
    """When a bar is in BOTH ``hollow_indices`` AND
    ``flat_overlay['bull_indices']``, the hollow treatment wins:
    the body renders hollow (alpha=0) and NO hatch overlay polygon
    is emitted for that bar."""
    candles = _uptrend_candles(4)
    fig, ax = plt.subplots()
    try:
        # Both overlays cover bar 1; only flat covers bar 0 too.
        overlay = _make_overlay(bull_indices=[0, 1])
        hollow = {1, 2}
        wicks, bodies = draw_candlesticks(
            ax, candles, start=0, end=4,
            hollow_indices=hollow,
            flat_overlay=overlay,
        )
        face_colors = bodies.get_facecolors()
        # Bar 1: hollow wins → alpha=0 on body.
        assert face_colors[1][3] == pytest.approx(0.0)
        # Bar 2: hollow only → alpha=0.
        assert face_colors[2][3] == pytest.approx(0.0)
        # Bar 0: not hollow → full alpha.
        assert face_colors[0][3] == pytest.approx(1.0)

        # Hatch overlay contains ONLY bar 0 (bar 1 was excluded by the
        # hollow > flat priority).
        hatch_cols = getattr(bodies, "_sc_flat_hatch_collections", [])
        assert len(hatch_cols) == 1
        assert len(hatch_cols[0].get_paths()) == 1

        # Both flags set so the fastpath bails.
        assert getattr(bodies, "_sc_hollow_mode", False) is True
        assert getattr(bodies, "_sc_accent_mode", False) is True
    finally:
        plt.close(fig)


def test_draw_candlesticks_hollow_only_skips_hatch_if_all_excluded():
    """If every bull-flat index is also hollow, NO hatch collection is
    added (no empty PolyCollection allocated)."""
    candles = _uptrend_candles(3)
    fig, ax = plt.subplots()
    try:
        overlay = _make_overlay(bull_indices=[0, 1, 2])
        hollow = {0, 1, 2}
        wicks, bodies = draw_candlesticks(
            ax, candles, start=0, end=3,
            hollow_indices=hollow,
            flat_overlay=overlay,
        )
        assert getattr(bodies, "_sc_flat_hatch_collections", []) == []
        # The accent-mode flag stays False since no hatch was actually added.
        assert getattr(bodies, "_sc_accent_mode", False) is False
    finally:
        plt.close(fig)


def test_draw_candlesticks_separate_bull_and_bear_hatch_collections():
    """Bull and bear flat sets produce two separate hatch PolyCollections
    (matplotlib hatch is per-collection, and the two sides can have
    different hatch colours)."""
    candles = _uptrend_candles(4)
    fig, ax = plt.subplots()
    try:
        overlay = _make_overlay(bull_indices=[1], bear_indices=[2])
        wicks, bodies = draw_candlesticks(
            ax, candles, start=0, end=4, flat_overlay=overlay
        )
        hatch_cols = getattr(bodies, "_sc_flat_hatch_collections", [])
        assert len(hatch_cols) == 2
        # First collection (bull) uses the bull hatch colour; second uses bear.
        bull_edge = hatch_cols[0].get_edgecolors()[0]
        bear_edge = hatch_cols[1].get_edgecolors()[0]
        np.testing.assert_allclose(bull_edge, (0.0, 0.0, 0.0, 1.0))
        np.testing.assert_allclose(bear_edge, (1.0, 1.0, 1.0, 1.0))
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# 3. _ha_flat_overlay_for — toggle gating
# ---------------------------------------------------------------------------


def test_helper_returns_none_when_toggle_off():
    """Toggle OFF → helper returns None regardless of HA mode."""
    app = _make_app_mock(ha_on=True, flat_on=False, dark_mode=False)
    out = app._ha_flat_overlay_for(_uptrend_candles(8))
    assert out is None


def test_helper_returns_none_when_ha_off():
    """HA OFF → helper returns None even if the flat toggle is on (HA-only feature)."""
    app = _make_app_mock(ha_on=False, flat_on=True, dark_mode=False)
    out = app._ha_flat_overlay_for(_uptrend_candles(8))
    assert out is None


def test_helper_returns_none_when_no_qualifying_bars():
    """No bull-flat-bottom and no bear-flat-top → helper returns None
    so the renderer skips the overlay path entirely (no allocations)."""
    # Doji-only series: ha_close == ha_open at every bar.
    candles = []
    t0 = _dt.datetime(2024, 1, 2, 9, 30)
    for i in range(5):
        candles.append(_candle(100.0, 100.5, 99.5, 100.0,
                               t=t0 + _dt.timedelta(minutes=i)))
    app = _make_app_mock(ha_on=True, flat_on=True, dark_mode=False)
    out = app._ha_flat_overlay_for(candles)
    assert out is None


def test_helper_returns_dict_when_qualifying_bars_present():
    """Both toggles ON + uptrend → dict with bull-flat-bottom indices
    populated, conforming to the renderer's flat_overlay schema."""
    app = _make_app_mock(ha_on=True, flat_on=True, dark_mode=False)
    out = app._ha_flat_overlay_for(_uptrend_candles(8))
    assert out is not None
    assert isinstance(out, dict)
    # Schema keys.
    for k in ("bull_indices", "bear_indices",
              "bull_color", "bear_color",
              "bull_hatch", "bear_hatch"):
        assert k in out, f"missing key {k!r} in overlay dict"
    # Uptrend has bull-flat bars, no bear-flat bars.
    assert len(out["bull_indices"]) >= 1
    # Indices are ints (so they survive __contains__ checks in rendering).
    for i in out["bull_indices"]:
        assert isinstance(i, int)
    # Hatch colours are 4-tuples in [0,1].
    for key in ("bull_color", "bear_color"):
        v = out[key]
        assert len(v) == 4
        for ch in v:
            assert 0.0 <= ch <= 1.0
    # Hatch patterns are strings.
    assert isinstance(out["bull_hatch"], str)
    assert isinstance(out["bear_hatch"], str)
    assert out["bull_hatch"]  # non-empty
    assert out["bear_hatch"]


def test_helper_uses_different_hatch_color_per_theme():
    """The bull hatch colour in dark mode differs from light mode
    (one comes from darker_shade, the other from brighter_shade)."""
    app_light = _make_app_mock(ha_on=True, flat_on=True, dark_mode=False)
    app_dark = _make_app_mock(ha_on=True, flat_on=True, dark_mode=True)

    out_light = app_light._ha_flat_overlay_for(_uptrend_candles(8))
    out_dark = app_dark._ha_flat_overlay_for(_uptrend_candles(8))
    assert out_light is not None
    assert out_dark is not None
    # Bull hatch line colour differs by theme.
    assert out_light["bull_color"] != out_dark["bull_color"]
    # Bear too.
    assert out_light["bear_color"] != out_dark["bear_color"]


def test_helper_empty_candles_returns_none():
    """Defensive: an empty candle list short-circuits to None."""
    app = _make_app_mock(ha_on=True, flat_on=True, dark_mode=False)
    assert app._ha_flat_overlay_for([]) is None


# ---------------------------------------------------------------------------
# Settings persistence default
# ---------------------------------------------------------------------------


def test_settings_default_is_on():
    """Per the feature design, the highlight defaults to ON when the
    setting has never been written. We verify the default arg of the
    BooleanVar construction by reading the source — the actual ``tk``
    call site needs a Tk root."""
    import inspect

    from tradinglab import app as app_mod
    src = inspect.getsource(app_mod.ChartApp.__init__)
    # The line we care about is:
    #     value=bool(_settings.get("highlight_ha_flat", True))
    assert '"highlight_ha_flat", True' in src, (
        "Highlight Flat HA Candles default must be ON (True)"
    )


def test_view_menu_lists_highlight_ha_flat():
    """The View menu wiring includes the new checkbutton with the
    canonical label ``Highlight Flat HA Candles`` and routes the
    ``command`` to the toggle handler."""
    import inspect
    from tradinglab import app as app_mod
    src = inspect.getsource(app_mod.ChartApp._build_menubar)
    assert "Highlight Flat HA Candles" in src
    assert "_on_menu_toggle_highlight_ha_flat" in src
    assert "_highlight_ha_flat_var" in src


# ---------------------------------------------------------------------------
# Menu-entry HA-gating wiring
# ---------------------------------------------------------------------------


def test_menu_state_sync_helper_exists():
    """``_sync_highlight_ha_flat_menu_state`` is the named seam the HA
    toggle handler calls to grey out / re-enable the flat-highlight
    menu entry. Its presence is the wiring contract: rename it and
    the HA toggle handler must be updated in lockstep."""
    from tradinglab.app import ChartApp
    assert hasattr(ChartApp, "_sync_highlight_ha_flat_menu_state"), (
        "ChartApp must expose _sync_highlight_ha_flat_menu_state")
    assert callable(getattr(ChartApp, "_sync_highlight_ha_flat_menu_state"))


def test_ha_toggle_handler_syncs_menu_state():
    """The HA toggle handler must call the sync helper so flipping
    HA candles immediately greys out (or re-enables) the flat
    highlight menu entry. Source-level check — exercising the
    handler proper needs a Tk root."""
    import inspect
    from tradinglab import app as app_mod
    src = inspect.getsource(app_mod.ChartApp._on_menu_toggle_heikin_ashi)
    assert "_sync_highlight_ha_flat_menu_state" in src, (
        "HA toggle handler must call _sync_highlight_ha_flat_menu_state "
        "so the Highlight Flat HA Candles menu entry follows HA mode")


def test_init_syncs_menu_state_at_startup():
    """``__init__`` must call the sync helper after ``_build_menubar``
    so the menu entry's disabled/normal state matches the persisted
    HA preference at app launch (otherwise the entry would always
    start in tk's default 'normal' state regardless of HA mode)."""
    import inspect
    from tradinglab import app as app_mod
    src = inspect.getsource(app_mod.ChartApp.__init__)
    assert "_sync_highlight_ha_flat_menu_state" in src, (
        "__init__ must call _sync_highlight_ha_flat_menu_state after "
        "_build_menubar to set the entry's initial state from "
        "_ha_display_var")
    # And the call must come AFTER _build_menubar (otherwise
    # _view_menu doesn't exist yet).
    build_at = src.index("_build_menubar()")
    sync_at = src.index("_sync_highlight_ha_flat_menu_state")
    assert build_at < sync_at, (
        "_sync_highlight_ha_flat_menu_state must be called AFTER "
        "_build_menubar() in __init__")


def _make_menu_mock_app(*, ha_on: bool):
    """Spin up a SimpleNamespace mock that the sync helper can drive
    without instantiating a real Tk root.

    We fake just enough of the ``tk.Menu`` surface
    (``index("end")``, ``type(i)``, ``entrycget(i, "label"|"state")``,
    ``entryconfigure(i, state=...)``) to drive the helper through
    its real code path.
    """
    class _FakeMenu:
        def __init__(self, entries):
            # entries: list of (type, label, state)
            self._entries = list(entries)

        def index(self, what):
            if what == "end":
                return len(self._entries) - 1
            return None

        def type(self, i):
            return self._entries[i][0]

        def entrycget(self, i, opt):
            _t, label, state = self._entries[i]
            if opt == "label":
                return label
            if opt == "state":
                return state
            raise ValueError(f"unsupported option {opt!r}")

        def entryconfigure(self, i, **kw):
            t, label, state = self._entries[i]
            if "state" in kw:
                state = kw["state"]
            self._entries[i] = (t, label, state)

    menu = _FakeMenu([
        ("checkbutton", "Heikin-Ashi Candles", "normal"),
        ("checkbutton", "Highlight Flat HA Candles", "normal"),
        ("checkbutton", "Highlight Key Bars", "normal"),
    ])
    ns = SimpleNamespace(
        _view_menu=menu,
        _ha_display_var=SimpleNamespace(get=lambda: ha_on),
    )
    from tradinglab.app import ChartApp
    ns._sync_highlight_ha_flat_menu_state = (
        ChartApp._sync_highlight_ha_flat_menu_state.__get__(ns)
    )
    return ns, menu


def test_sync_helper_disables_entry_when_ha_off():
    """HA off → entry's ``state`` becomes ``"disabled"``."""
    ns, menu = _make_menu_mock_app(ha_on=False)
    ns._sync_highlight_ha_flat_menu_state()
    assert menu.entrycget(1, "state") == "disabled"
    # Other entries untouched.
    assert menu.entrycget(0, "state") == "normal"
    assert menu.entrycget(2, "state") == "normal"


def test_sync_helper_enables_entry_when_ha_on():
    """HA on → entry's ``state`` becomes ``"normal"``."""
    ns, menu = _make_menu_mock_app(ha_on=True)
    # Pre-disable so we can verify the helper flips it back.
    menu.entryconfigure(1, state="disabled")
    ns._sync_highlight_ha_flat_menu_state()
    assert menu.entrycget(1, "state") == "normal"


def test_sync_helper_noop_when_view_menu_missing():
    """Defensive: helper must not raise if called before
    ``_build_menubar`` (or after Tk shutdown). It should silently
    no-op rather than crash."""
    from tradinglab.app import ChartApp
    ns = SimpleNamespace(_ha_display_var=SimpleNamespace(get=lambda: True))
    # Bind without _view_menu attribute at all.
    ChartApp._sync_highlight_ha_flat_menu_state(ns)  # should not raise


def test_sync_helper_preserves_underlying_var():
    """The sync helper changes only the *menu entry's* state — it
    must NOT touch the underlying BooleanVar. So the user's
    "highlight on" preference persists across HA-off intervals
    and re-engages on HA-on."""
    ns, menu = _make_menu_mock_app(ha_on=False)
    # Add a BooleanVar-ish stand-in and confirm it isn't touched.
    ns._highlight_ha_flat_var = SimpleNamespace(get=lambda: True)
    ns._sync_highlight_ha_flat_menu_state()
    # Var still reports True (the user's preference).
    assert ns._highlight_ha_flat_var.get() is True
    # But the menu entry is greyed out.
    assert menu.entrycget(1, "state") == "disabled"
