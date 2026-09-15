"""Adversarial fixtures prove the shared probe detects clipping, not requests."""
from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk

import pytest

from tests._window_width import _horizontal_scroll, assert_window_width, enlarged_fonts, mapped_window
from tradinglab.gui._modal_base import BaseModalDialog, make_scrollable_form


def test_rejects_unmapped_one_pixel_window(root):
    ttk.Label(root, text="Not evidence").pack()
    with pytest.raises(AssertionError, match="mapped"):
        assert_window_width(root)


def test_rejects_clipped_button_in_allocated_frame(root):
    root.geometry("220x160")
    frame = ttk.Frame(root, width=90, height=80)
    frame.pack_propagate(False)
    frame.pack()
    ttk.Button(frame, text="This complete action must remain readable").pack(fill="x")
    with mapped_window(root), pytest.raises(AssertionError, match="usable width"):
        assert_window_width(root)


def test_rejects_control_outside_intermediate_ancestor(root):
    root.geometry("400x160")
    frame = ttk.Frame(root, width=80, height=80)
    frame.pack_propagate(False)
    frame.pack()
    ttk.Button(frame, text="Action").place(x=60, y=10, width=100)
    with mapped_window(root), pytest.raises(AssertionError, match="outside"):
        assert_window_width(root)


def test_accepts_wrapped_label_and_data_viewports(root):
    root.geometry("230x260")
    ttk.Label(root, text="A long explanation that wraps rather than clips horizontally.",
              wraplength=180).pack(fill="x")
    ttk.Entry(root, width=90).pack(fill="x")
    tk.Text(root, width=90, height=2).pack(fill="x")
    tk.Canvas(root, width=900, height=30).pack(fill="x")
    with mapped_window(root):
        assert assert_window_width(root) == 4


@pytest.mark.parametrize("horizontal", [False, True])
def test_canvas_form_requires_real_horizontal_reachability(root, horizontal):
    root.geometry("250x200")
    inner, canvas = make_scrollable_form(root, horizontal=horizontal, bind_mousewheel=False)
    ttk.Button(inner, text="A form action much wider than the narrow outer canvas").pack(fill="x")
    with mapped_window(root):
        if horizontal:
            assert assert_window_width(root) > 0
        else:
            with pytest.raises(AssertionError, match="usable width|outside"):
                assert_window_width(root)


@pytest.mark.parametrize("broken", ["command", "thumb", "hidden"])
def test_rejects_broken_or_unreachable_horizontal_scrollbar(root, broken):
    root.geometry("250x200")
    inner, canvas = make_scrollable_form(root, horizontal=True, bind_mousewheel=False)
    ttk.Button(inner, text="A form action much wider than the narrow outer canvas").pack()
    bar = next(w for w in root.winfo_children()
               if isinstance(w, ttk.Scrollbar) and str(w.cget("orient")) == "horizontal")
    if broken == "command":
        bar.configure(command=lambda *_: None)
    elif broken == "thumb":
        canvas.configure(xscrollcommand=lambda *_: None)
    else:
        bar.pack_forget()
    with mapped_window(root), pytest.raises(AssertionError, match="outside"):
        assert_window_width(root)


def test_treeview_overflow_is_not_excused_as_a_viewport(root):
    root.geometry("220x200")
    tree = ttk.Treeview(root, columns=("value",))
    tree.pack(fill="both", expand=True)
    tree.column("#0", width=250, stretch=False)
    tree.column("value", width=250, stretch=False)
    with mapped_window(root), pytest.raises(AssertionError, match="horizontal scrolling"):
        assert_window_width(root)


def test_named_label_elision_keeps_bounds_and_rejects_stale_policy(root):
    root.geometry("180x150")
    label = ttk.Label(root, text="A deliberately elided dynamic status message")
    label.pack(fill="x")
    with mapped_window(root):
        with pytest.raises(AssertionError, match="usable width"):
            assert_window_width(root)
        assert_window_width(root, elided_labels={label: "Summary; full value is in the details view."})
        label.pack_forget()
        with pytest.raises(AssertionError, match="Stale"):
            assert_window_width(root, elided_labels={label: "No longer visible"})


def test_enlarged_fonts_restore_shared_interpreter_after_failure(root):
    before = font.nametofont("TkDefaultFont", root=root).actual("size")
    with pytest.raises(RuntimeError), enlarged_fonts(root):
        assert font.nametofont("TkDefaultFont", root=root).actual("size") == 16
        raise RuntimeError("fixture body failed")
    assert font.nametofont("TkDefaultFont", root=root).actual("size") == before


def test_horizontal_probe_does_not_scroll_an_unrelated_view(root):
    root.geometry("260x400")
    canvases = []
    for _ in range(2):
        host = ttk.Frame(root)
        host.pack(fill="both", expand=True)
        inner, canvas = make_scrollable_form(host, horizontal=True, bind_mousewheel=False)
        ttk.Button(inner, text="A long action requiring horizontal scrolling to read in full").pack()
        canvases.append(canvas)
    with mapped_window(root):
        canvases[0].xview_moveto(0.2)
        before = [canvas.xview() for canvas in canvases]
        assert _horizontal_scroll(canvases[1], root)
        assert [canvas.xview() for canvas in canvases] == before


@pytest.mark.parametrize("region_width", [500, 1000])
def test_scrollregion_must_reach_actual_embedded_form(root, region_width):
    root.geometry("250x200")
    canvas = tk.Canvas(root, highlightthickness=0, scrollregion=(0, 0, region_width, 100))
    bar = ttk.Scrollbar(root, orient="horizontal", command=canvas.xview)
    bar.pack(side="bottom", fill="x")
    canvas.pack(fill="both", expand=True)
    canvas.configure(xscrollcommand=bar.set)
    inner = ttk.Frame(canvas, width=1000, height=100)
    inner.pack_propagate(False)
    canvas.create_window(0, 0, anchor="nw", window=inner)
    ttk.Button(inner, text="Far right action").pack(side="right")
    with mapped_window(root):
        if region_width == 1000:
            assert_window_width(root)
        else:
            with pytest.raises(AssertionError, match="outside"):
                assert_window_width(root)


def test_empty_structural_frame_is_not_a_one_pixel_control(root):
    ttk.Frame(root).pack()
    ttk.Button(root, text="Visible").pack()
    with mapped_window(root):
        assert assert_window_width(root) == 1


@pytest.mark.parametrize("canvas_form", [False, True])
def test_opt_in_form_floor_clamps_stale_width_without_changing_height(root, canvas_form):
    dialog = BaseModalDialog(root, default_geometry="180x230+10+20")
    try:
        if canvas_form:
            form, canvas = make_scrollable_form(dialog, bind_mousewheel=False)
        else:
            form, canvas = dialog, None
        ttk.Button(form, text="A complete action label that must not be cut off").pack(fill="x")
        width = dialog._fit_form_width(
            **({"form": form, "viewport": canvas} if canvas_form else {}),
        )
        assert dialog._default_geometry == f"{width}x230+10+20"
        dialog.geometry("120x230")
        with mapped_window(dialog):
            assert dialog.winfo_width() >= width
            assert_window_width(dialog)
    finally:
        dialog.destroy()


def test_form_sizing_is_opt_in_and_requires_both_canvas_arguments(root):
    dialog = BaseModalDialog(root, default_geometry="220x180")
    try:
        tk.Canvas(dialog, width=3000).pack()
        dialog._finalize_modal(grab=False)
        assert dialog._default_geometry == "220x180"
        with pytest.raises(ValueError, match="together"):
            dialog._fit_form_width(form=dialog)
    finally:
        dialog.destroy()
