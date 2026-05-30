"""Headless tests for ``_FieldRefParamDialog`` (the compact-picker Edit popup).

Pins:
* Apply returns a new ``FieldRef`` with the edited params, preserving the
  ref's identity (id / output_key / cross-symbol ``symbol`` / interval).
* Cancel returns ``None`` (no mutation leaks).
* Invalid input blocks Apply and surfaces a validation message.
* Multi-output indicators expose an output-key selector that round-trips.
* The cross-symbol ``FieldRef.symbol`` pin is preserved untouched (NOT edited
  in this popup).
"""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("tkinter.ttk")

from tradinglab.gui import scanner_block_editor as sbe
from tradinglab.scanner.model import FieldRef


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("1x1-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


def _mk(root, ref):
    dlg = sbe._FieldRefParamDialog(root, ref=ref)
    root.update_idletasks()
    return dlg


def test_apply_returns_updated_ref_preserving_identity(root):
    ref = FieldRef.indicator(
        "rrvol", params={"length": 20}, symbol="AAPL", interval="5m"
    )
    dlg = _mk(root, ref)
    dlg._param_vars["length"].set("30")
    dlg._param_vars["compare_symbol"].set("QQQ")
    dlg._on_primary()
    res = dlg.result
    assert res is not None
    assert res.id == "rrvol"
    assert res.params["length"] == 30
    assert res.params["compare_symbol"] == "QQQ"
    # cross-symbol pin + interval preserved untouched.
    assert res.symbol == "AAPL"
    assert res.interval == "5m"


def test_cancel_returns_none(root):
    ref = FieldRef.indicator("rrvol", params={"length": 20})
    dlg = _mk(root, ref)
    dlg._param_vars["length"].set("99")
    dlg._on_cancel()
    assert dlg.result is None


def test_invalid_blocks_apply_and_surfaces_message(root):
    ref = FieldRef.indicator("rvol", params={"length": 20})
    dlg = _mk(root, ref)
    dlg._param_vars["length"].set("not-a-number")
    dlg._on_primary()
    assert dlg.result is None
    assert dlg._error_var.get()  # non-empty validation message


def test_multi_output_round_trips_output_key(root):
    ref = FieldRef.indicator("smi", output_key="smi")
    dlg = _mk(root, ref)
    assert dlg._output_var is not None
    dlg._output_var.set("signal")
    dlg._on_primary()
    assert dlg.result is not None
    assert dlg.result.output_key == "signal"


def test_single_output_has_no_output_selector(root):
    ref = FieldRef.indicator("rvol", params={"length": 20})
    dlg = _mk(root, ref)
    assert dlg._output_var is None
    dlg._on_primary()
    assert dlg.result is not None


def test_param_vars_seeded_from_ref(root):
    ref = FieldRef.indicator("rvol", params={"length": 14})
    dlg = _mk(root, ref)
    assert dlg._param_vars["length"].get() in ("14", 14)


def _all_label_texts(widget):
    texts = []
    for child in widget.winfo_children():
        try:
            if isinstance(child, tk.ttk.Label):  # type: ignore[attr-defined]
                texts.append(str(child.cget("text")))
        except Exception:  # noqa: BLE001
            pass
        texts.extend(_all_label_texts(child))
    return texts


def test_general_description_not_rendered(root):
    """The indicator's general description must NOT appear in the Edit popup —
    params sit at the top for a cleaner, more user-friendly layout."""
    from tradinglab.scanner.fields import get_field

    spec = get_field("rrvol", kind="indicator")
    assert spec is not None and spec.description  # precondition: rrvol has one
    dlg = _mk(root, FieldRef.indicator("rrvol", params={"length": 20}))
    labels = _all_label_texts(dlg)
    assert spec.description not in labels


def _find_info_icons(widget):
    """Collect every info-icon Label (text == the circled-i glyph)."""
    icons = []
    for child in widget.winfo_children():
        try:
            if isinstance(child, tk.ttk.Label) and str(child.cget("text")) == sbe._INFO_ICON_GLYPH:  # type: ignore[attr-defined]
                icons.append(child)
        except Exception:  # noqa: BLE001
            pass
        icons.extend(_find_info_icons(child))
    return icons


def test_each_param_has_info_icon_with_description(root):
    """Every parameter row exposes an (i) info icon whose hover tooltip
    carries that parameter's description."""
    from tradinglab.scanner.fields import get_field

    spec = get_field("rrvol", kind="indicator")
    assert spec is not None
    dlg = _mk(root, FieldRef.indicator("rrvol", params={"length": 20}))

    # One info tooltip per param that has descriptive text.
    for pdef in spec.params_schema:
        tip = sbe.tooltip_text_for(pdef)
        if tip:
            assert pdef.name in dlg._info_tooltips
            assert dlg._info_tooltips[pdef.name]._text == tip

    # And visible (i) icon widgets are actually present in the form.
    icons = _find_info_icons(dlg)
    assert len(icons) >= 1


def test_info_icon_tooltip_glyph_is_circled_i(root):
    dlg = _mk(root, FieldRef.indicator("rrvol", params={"length": 20}))
    icons = _find_info_icons(dlg)
    assert icons, "expected at least one (i) info icon"
    assert all(str(ic.cget("text")) == sbe._INFO_ICON_GLYPH for ic in icons)


def _force_fit(dlg, root):
    """Realize geometry so the canvas viewport is taller than its content."""
    try:
        root.deiconify()
        dlg.deiconify()
        dlg.geometry("380x460")
        dlg.update()
        dlg.update_idletasks()
    except tk.TclError:
        pass


def test_single_param_form_does_not_scroll(root):
    """LRSI has a single parameter that fully fits the popup — the wheel
    must NOT drag the lone widget around (the reported bug)."""
    dlg = _mk(root, FieldRef.indicator("lrsi"))
    _force_fit(dlg, root)
    canvas = dlg._form_canvas
    # When content fits, the view fraction spans the whole region.
    first, last = canvas.yview()
    assert float(first) <= 0.0 and float(last) >= 1.0
    # The wheel-scroll guard refuses to scroll a fitting form.
    assert canvas._tl_v_can_scroll() is False
    before = canvas.yview()
    handler = canvas._tl_wheel_handler

    class _Evt:
        delta = -120

    handler(_Evt())
    handler(_Evt())
    assert canvas.yview() == before


def test_scrollable_form_guard_blocks_when_fitting(root):
    """Unit-level: make_scrollable_form refuses wheel scroll when content
    fits the viewport, generalizing the fix to every dialog that uses it."""
    from tradinglab.gui._modal_base import make_scrollable_form

    host = tk.ttk.Frame(root)  # type: ignore[attr-defined]
    host.pack(fill="both", expand=True)
    inner, canvas = make_scrollable_form(host)
    tk.ttk.Label(inner, text="only one row").pack()  # type: ignore[attr-defined]
    try:
        root.geometry("400x500")
        root.deiconify()
        root.update()
        root.update_idletasks()
    except tk.TclError:
        pass
    assert canvas._tl_v_can_scroll() is False


