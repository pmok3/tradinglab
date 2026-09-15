from __future__ import annotations

from tkinter import ttk

import pytest

from tests._window_width import assert_window_width, enlarged_fonts, mapped_window
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
        assert len({button.winfo_rooty() for button in buttons}) > 1
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
