"""Live theme swap recolors the in-readout overlay legend names.

Regression: the per-overlay legend rows (the indicator NAME labels shown on the
price pane, e.g. ``EMA(9)``) bake their colour into the ``TextArea`` at build
time (``interaction._build_readout_indicator_rows``). Switching light→dark used
to leave them their old colour (black-on-dark) until the next full ``_render``
— the user only saw them flip after opening "Manage Indicators" (which forces a
re-render). ``ThemeController._apply_overlay_artists`` now recolors them in
place on every theme swap:

* a VISIBLE row's ``label_textarea`` → ``theme["text"]`` (indicator value
  segments keep their own theme-independent colour);
* a HIDDEN row → every child ``TextArea`` → the muted colour.

These tests drive ``_apply_overlay_artists`` directly against hand-built
offsetbox artists (no Tk root needed) — the method only reads ``self._root``.
"""
from __future__ import annotations

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")

from matplotlib.offsetbox import HPacker, TextArea  # noqa: E402

from tradinglab.gui.theme_controller import ThemeController  # noqa: E402

_TEXT = "#e0e0e0"      # dark-theme text
_MUTED = "#888888"     # dark-theme muted
_LIGHT = "#000000"     # stale light-mode colour (the bug)
_IND = "#1f77b4"       # an indicator's own (theme-independent) line colour
_THEME = {"text": _TEXT, "muted": _MUTED}


def _ta(color: str) -> TextArea:
    return TextArea("x ", textprops=dict(color=color, fontsize=9, family="monospace"))


def _controller_for(readout_artists: dict) -> ThemeController:
    # Bypass __init__ (which wants a Tk root + settings); _apply_overlay_artists
    # only touches self._root.
    tc = ThemeController.__new__(ThemeController)
    tc._root = SimpleNamespace(_readout_artists=readout_artists)
    return tc


def _color(ta: TextArea) -> str:
    return ta._text.get_color()


def test_visible_overlay_name_recolors_value_segment_preserved():
    label = _ta(_LIGHT)
    value = _ta(_IND)
    container = HPacker(children=[label, value], align="center", pad=0, sep=0)
    box = SimpleNamespace(
        _main_text=_ta(_LIGHT),
        _ind_rows=[{
            "config_id": 1, "visible": True, "label_textarea": label,
            "container": container,
            "outputs": [{"value_textarea": value, "color": _IND}],
        }],
    )
    _controller_for({"ax": box})._apply_overlay_artists(_THEME)
    assert _color(label) == _TEXT, "visible overlay NAME must follow the theme text colour"
    assert _color(box._main_text) == _TEXT, "OHLCV strip must follow the theme too"
    assert _color(value) == _IND, "a visible indicator's value colour is theme-independent"


def test_hidden_overlay_row_is_fully_muted():
    label = _ta(_LIGHT)
    value = _ta(_LIGHT)
    container = HPacker(children=[label, value], align="center", pad=0, sep=0)
    box = SimpleNamespace(
        _main_text=_ta(_LIGHT),
        _ind_rows=[{
            "config_id": 2, "visible": False, "label_textarea": label,
            "container": container,
            "outputs": [{"value_textarea": value, "color": _MUTED}],
        }],
    )
    _controller_for({"ax": box})._apply_overlay_artists(_THEME)
    assert _color(label) == _MUTED
    assert _color(value) == _MUTED


def test_multiple_rows_and_boxes_all_recolor():
    rows = []
    labels = []
    for vis in (True, False, True):
        lab = _ta(_LIGHT)
        labels.append((lab, vis))
        rows.append({
            "config_id": len(rows), "visible": vis, "label_textarea": lab,
            "container": HPacker(children=[lab, _ta(_IND)], align="center", pad=0, sep=0),
            "outputs": [],
        })
    box = SimpleNamespace(_main_text=_ta(_LIGHT), _ind_rows=rows)
    _controller_for({"ax": box})._apply_overlay_artists(_THEME)
    for lab, vis in labels:
        assert _color(lab) == (_TEXT if vis else _MUTED)


def test_box_without_ind_rows_does_not_raise():
    # Back-compat: an OHLCV-only readout (no overlay legend rows yet).
    box = SimpleNamespace(_main_text=_ta(_LIGHT))
    _controller_for({"ax": box})._apply_overlay_artists(_THEME)
    assert _color(box._main_text) == _TEXT


def test_muted_falls_back_when_theme_lacks_muted_key():
    label = _ta(_LIGHT)
    box = SimpleNamespace(
        _main_text=_ta(_LIGHT),
        _ind_rows=[{
            "config_id": 1, "visible": False, "label_textarea": label,
            "container": HPacker(children=[label], align="center", pad=0, sep=0),
            "outputs": [],
        }],
    )
    # No "muted" key → falls back to "axis" then the hardcoded grey.
    _controller_for({"ax": box})._apply_overlay_artists({"text": _TEXT, "axis": "#777777"})
    assert _color(label) == "#777777"
