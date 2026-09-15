"""Real mapped row rebuilds must refit without a subsequent user resize."""
from __future__ import annotations

import time
from contextlib import nullcontext

import pytest

from tests._window_width import assert_window_width, enlarged_fonts, mapped_window
from tradinglab.gui._modal_base import protect_combobox_wheel
from tradinglab.gui.scanner_block_editor import BlockEditor
from tradinglab.scanner.model import OP_GT, OP_INSIDE_BAR, Condition, FieldRef, Group


def _settle(window):
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        window.update()
        time.sleep(0.01)


@pytest.mark.parametrize("large_font", [False, True])
def test_operator_rebuild_refits_new_controls_without_window_resize(root, large_font):
    with enlarged_fonts(root) if large_font else nullcontext():
        root.geometry("850x700")
        changes = []

        def on_change():
            changes.append(protect_combobox_wheel(root))

        condition = Condition(left=FieldRef.builtin("close"), op=OP_INSIDE_BAR, params={})
        editor = BlockEditor(root, root=Group(children=[condition]), on_change=on_change)
        editor.pack(fill="both", expand=True)
        with mapped_window(root):
            _settle(root)
            before = root.winfo_geometry()
            row = editor._root_frame._child_frames[0]
            row._op_var.set(OP_GT)
            row._on_op_change()
            _settle(root)
            assert root.winfo_geometry() == before
            assert condition.op == OP_GT
            assert changes and changes[-1] > 0
            assert row._param_widgets["right"][1]._type_combo.bind("<MouseWheel>")
            assert_window_width(root)
