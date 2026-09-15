from __future__ import annotations

from tkinter import ttk

import pytest

from tests._window_width import assert_window_width, enlarged_fonts, mapped_window
from tradinglab.gui._modal_base import make_scrollable_form
from tradinglab.gui.flow_layout import wrap_controls


def _row(root):
    row = ttk.Frame(root)
    row.pack(fill="x")
    buttons = [ttk.Button(row, text=text) for text in ("New", "Rename", "Delete", "Import", "Export")]
    for button in buttons:
        button.pack(side="left")
    return row, buttons


def test_shrink_grow_preserves_controls_and_state(root):
    root.geometry("600x300")
    row, buttons = _row(root)
    calls = []
    buttons[0].configure(command=lambda: calls.append("new"))
    buttons[2].state(["disabled"])
    layout = wrap_controls(row)
    with mapped_window(root):
        before = [str(button) for button in buttons]
        for width in (240, 600, 240):
            root.geometry(f"{width}x300")
            root.update()
            assert_window_width(root)
            assert [str(button) for button in buttons] == before
            assert buttons[2].instate(["disabled"])
            assert all(button.winfo_ismapped() for button in buttons)
            if width == 600:
                assert len({button.winfo_rooty() for button in buttons}) == 1
        assert len({button.winfo_rooty() for button in buttons}) > 1
        buttons[0].invoke()
        assert calls == ["new"]
        assert layout._job is None


def test_enlarged_fonts_wrap_without_losing_buttons(root):
    root.geometry("300x500")
    row, buttons = _row(root)
    wrap_controls(row)
    with mapped_window(root), enlarged_fonts(root, size=16):
        root.update()
        assert_window_width(root)
        assert all(button.winfo_ismapped() for button in buttons)
        assert len({button.winfo_rooty() for button in buttons}) > 1


def test_hidden_controls_are_not_resurrected(root):
    root.geometry("300x400")
    row, buttons = _row(root)
    buttons[0].pack_forget()
    layout = wrap_controls(row)
    with mapped_window(root):
        buttons[1].pack_forget()
        for width in (250, 500):
            root.geometry(f"{width}x400")
            root.update()
            assert not buttons[0].winfo_manager()
            assert not buttons[1].winfo_manager()
            assert_window_width(root)
        buttons[1].pack(side="left")
        layout._schedule()
        root.update()
        assert buttons[1].winfo_ismapped()
        assert not buttons[0].winfo_manager()


def test_destroy_cancels_owned_callback(root):
    row, _buttons = _row(root)
    layout = wrap_controls(row)
    job = layout._job
    assert job is not None
    row.destroy()
    assert layout._closed and layout._job is None
    assert job not in root.tk.splitlist(root.tk.call("after", "info"))
    root.update()


def test_explicit_initially_hidden_control_can_join_flow(root):
    root.geometry("260x400")
    row, buttons = _row(root)
    buttons[0].pack_forget()
    wrap_controls(row, controls=buttons)
    with mapped_window(root):
        assert not buttons[0].winfo_manager()
        buttons[0].pack(side="left")
        root.update()
        root.geometry("240x400")
        root.update()
        assert_window_width(root)
        assert buttons[0].winfo_ismapped()
        assert buttons[0].winfo_rooty() <= buttons[1].winfo_rooty()


def test_flow_uses_row_allocation_inside_padding(root):
    root.geometry("320x400")
    row, buttons = _row(root)
    row.configure(padding=30)
    wrap_controls(row)
    with mapped_window(root):
        root.update()
        assert_window_width(root)
        assert all(button.winfo_ismapped() for button in buttons)


def test_other_rows_are_unchanged_and_single_oversize_control_is_honest(root):
    row, buttons = _row(root)
    original = [button.pack_info() for button in buttons]
    root.update_idletasks()
    assert [button.pack_info() for button in buttons] == original
    row.destroy()
    row = ttk.Frame(root)
    row.pack(fill="x")
    button = ttk.Button(row, text="An action wider than this very narrow host")
    button.pack(side="left")
    wrap_controls(row)
    root.geometry("120x200")
    with mapped_window(root):
        root.update()
        assert button.winfo_reqwidth() > button.winfo_width()
        with pytest.raises(AssertionError, match="usable width"):
            assert_window_width(root)


def test_unmapped_flow_preserves_natural_requests_and_resumes_on_map(root, monkeypatch):
    root.geometry("240x400")
    row, buttons = _row(root)
    root.update_idletasks()
    original_request = row.winfo_reqwidth()
    layout = wrap_controls(row)
    # Embedded Canvas children can retain this flag under a withdrawn top.
    monkeypatch.setattr(row, "winfo_ismapped", lambda: True)
    root.update_idletasks()
    assert not root.winfo_ismapped()
    assert layout._signature is None
    assert layout._job is None
    assert row.winfo_reqwidth() == original_request
    assert all(button.pack_info()["in"] is row for button in buttons)
    with mapped_window(root):
        assert_window_width(root)
        assert len({button.winfo_rooty() for button in buttons}) > 1
        assert layout._job is None


def test_remapping_reflows_font_changes_and_preserves_unrelated_map_binding(root):
    root.geometry("320x500")
    row, buttons = _row(root)
    events = []
    unrelated = root.bind("<Map>", lambda event: events.append(event.widget))
    layout = wrap_controls(row)
    with mapped_window(root):
        root.withdraw()
        root.update()
        signature = layout._signature
        with enlarged_fonts(root, size=16):
            root.update_idletasks()
            assert layout._signature == signature
            root.deiconify()
            root.update()
            assert_window_width(root)
            assert all(button.winfo_ismapped() for button in buttons)
            assert layout._signature != signature
            assert unrelated in root.bind("<Map>")
            assert root in events
            owned = next(binding for widget, sequence, binding in layout._bindings
                         if widget is root and sequence == "<Map>")
            row.destroy()
            assert owned not in root.bind("<Map>")
            assert unrelated in root.bind("<Map>")


def test_inactive_notebook_canvas_waits_for_ancestor_map(root, monkeypatch):
    root.geometry("320x500")
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    first, second = ttk.Frame(notebook), ttk.Frame(notebook)
    notebook.add(first, text="First")
    notebook.add(second, text="Flow")
    ttk.Button(first, text="Visible first page").pack()
    inner, _canvas = make_scrollable_form(second, horizontal=True, bind_mousewheel=False)
    row, buttons = _row(inner)
    layout = wrap_controls(row)
    with mapped_window(root):
        notebook.select(second)
        root.update()
        signature = layout._signature
        assert signature is not None
        notebook.select(first)
        root.update()
        monkeypatch.setattr(row, "winfo_ismapped", lambda: True)
        assert not row.winfo_viewable()
        with enlarged_fonts(root, size=16):
            root.update_idletasks()
            assert layout._signature == signature
            notebook.select(second)
            root.update()
            assert row.winfo_viewable()
            assert layout._signature != signature
            assert all(button.winfo_viewable() for button in buttons)
            assert_window_width(root)
