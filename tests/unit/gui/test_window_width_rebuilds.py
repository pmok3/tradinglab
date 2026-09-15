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


def test_entries_rvol_relayout_converges_while_withdrawn_then_mapped(root, tmp_path, monkeypatch):
    from tradinglab.entries.model import EntryStrategy, TriggerKind
    from tradinglab.gui import _modal_base, geometry_store
    from tradinglab.gui.entries_dialog import EntriesDialog
    from tradinglab.gui.flow_layout import FlowLayout

    store = geometry_store.GeometryStore(tmp_path / "geometry.json")
    store.load()
    monkeypatch.setattr(_modal_base, "_gstore", lambda: store)
    original_jobs = set(root.tk.call("after", "info"))
    calls = []
    original_reflow = FlowLayout._reflow

    def reflow(layout):
        calls.append(layout.container)
        original_reflow(layout)

    monkeypatch.setattr(FlowLayout, "_reflow", reflow)
    dialog = EntriesDialog(root, strategy=EntryStrategy(name="RVOL layout convergence"))
    try:
        dialog.geometry("1200x780+50+50")
        dialog.update_idletasks()
        dialog._draft.trigger.kind = TriggerKind.INDICATOR
        dialog._draft.trigger.condition = Group(children=[
            Condition(left=FieldRef.indicator("rvol"), op=OP_GT,
                      params={"right": FieldRef.literal(1.5)}, interval="5m"),
        ])
        dialog._render_trigger_params()
        dialog.update_idletasks()
        row = dialog.block_editor._root_frame._child_frames[0]
        row._relayout_if_needed()
        dialog.update_idletasks()
        assert not dialog.winfo_ismapped()
        assert row._left_picker._display_mode == "compact"
        with mapped_window(dialog):
            for width in (1200, 900, 1200):
                dialog.geometry(f"{width}x780+50+50")
                _settle(dialog)
                row._relayout_if_needed()
                _settle(dialog)
                assert_window_width(dialog)
                before = len(calls)
                for _ in range(5):
                    dialog.update_idletasks()
                assert len(calls) == before, "Settled layout kept scheduling idle flow work"
                assert row._delete_btn.winfo_ismapped()
                assert row._left_picker._indicator_combo.winfo_ismapped()
    finally:
        dialog.destroy()
        for job in set(root.tk.call("after", "info")) - original_jobs:
            root.after_cancel(job)
