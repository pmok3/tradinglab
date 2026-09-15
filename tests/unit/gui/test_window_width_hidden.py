"""Fully starved control rows differ from intentionally hidden UI sections."""
import tkinter as tk
from tkinter import ttk

import pytest

from tests._window_width import assert_window_width, mapped_window


@pytest.mark.parametrize("ancestor", [False, True])
def test_rejects_fully_unallocated_managed_action(root, ancestor):
    root.geometry("180x150")
    body = ttk.Frame(root, width=180, height=150)
    body.pack_propagate(False)
    body.pack(side="left", fill="both", expand=True)
    ttk.Label(body, text="Visible body").pack()
    host = ttk.Frame(root) if ancestor else root
    if ancestor:
        host.pack(side="left")
    button = ttk.Button(host, text="Missing action")
    button.pack(side="left")
    with mapped_window(root):
        assert not button.winfo_ismapped()
        with pytest.raises(AssertionError, match="not allocated"):
            assert_window_width(root)


@pytest.mark.parametrize("manager", ["pack", "grid"])
def test_explicitly_hidden_ancestor_is_not_a_starved_action(root, manager):
    ttk.Button(root, text="Visible").pack()
    holder = ttk.Frame(root)
    hidden = ttk.Frame(holder)
    getattr(hidden, manager)()
    ttk.Button(hidden, text="Intentionally hidden").pack()
    if manager == "pack":
        hidden.pack_forget()
    else:
        hidden.grid_remove()
    holder.pack()
    with mapped_window(root):
        assert_window_width(root)


def test_notebook_page_requires_explicit_selection_to_supply_evidence(root):
    root.geometry("240x180")
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    for name in ("Visible", "Hidden"):
        page = ttk.Frame(notebook)
        notebook.add(page, text=name)
        if name == "Visible":
            ttk.Button(page, text=name).pack()
        else:
            ttk.Button(page, text="A far too wide action when this page is selected").pack(fill="x")
    with mapped_window(root):
        assert_window_width(root)
        notebook.select(1)
        root.update()
        with pytest.raises(AssertionError, match="usable width"):
            assert_window_width(root)


def test_in_geometry_parent_not_only_widget_master_clips_controls(root):
    root.geometry("300x160")
    frame = ttk.Frame(root, width=70, height=70)
    frame.pack_propagate(False)
    frame.pack()
    button = ttk.Button(root, text="A button allocated inside a smaller ancestor")
    button.pack(in_=frame)
    with mapped_window(root), pytest.raises(AssertionError, match="usable width|outside"):
        assert_window_width(root)


def test_hidden_notebook_ancestor_wins_over_canvas_child_mapped_flag(root, monkeypatch):
    root.geometry("240x180")
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    first, second = ttk.Frame(notebook), ttk.Frame(notebook)
    notebook.add(first, text="Visible")
    notebook.add(second, text="Canvas")
    ttk.Button(first, text="Visible action").pack()
    canvas = tk.Canvas(second)
    canvas.pack(fill="both", expand=True)
    form = ttk.Frame(canvas)
    canvas.create_window(0, 0, anchor="nw", window=form)
    button = ttk.Button(form, text="An oversized action in an inactive canvas-backed notebook page")
    button.pack()
    with mapped_window(root):
        notebook.select(second)
        root.update()
        notebook.select(first)
        root.update()
        # Windows Tk can retain mapped flags on embedded Canvas windows.
        monkeypatch.setattr(form, "winfo_ismapped", lambda: True)
        monkeypatch.setattr(button, "winfo_ismapped", lambda: True)
        assert_window_width(root)
        notebook.select(second)
        root.update()
        with pytest.raises(AssertionError, match="outside"):
            assert_window_width(root)
