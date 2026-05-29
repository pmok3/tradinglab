"""Geometric layout tests for the auto-stack ConditionFrame (CLAUDE.md §7.19).

These tests render the row inside a sized Toplevel (1200px-wide,
matching a typical 1400px dialog minus chrome) and verify that the
right-most reachable widget in each layout sits within the parent's
right edge. This pins the bug the auto-stack design exists to fix:
RVOL's 6-param picker pushing the RHS picker / lookback cluster /
interval combo / delete button off the canvas of a non-scrollable
dialog.

The geometric assertions are intentionally permissive (allow a small
tolerance) because Tk's idletask layout pass isn't perfectly
deterministic across platforms — measuring from the inner frame's
own ``winfo_width`` after ``update_idletasks`` is the most stable
signal.
"""

from __future__ import annotations

import tkinter as tk

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import (
    BlockEditor,
    _ConditionFrame,
    _FieldRefPicker,
)
from tradinglab.scanner.model import (
    OP_BETWEEN,
    OP_GT,
    Condition,
    FieldRef,
    Group,
)

_DIALOG_WIDTH_PX: int = 1200


def _make_sized_editor(parent: tk.Misc, cond: Condition) -> BlockEditor:
    """Mount a BlockEditor inside a 1200x600 Toplevel + force layout.

    Returns the editor; caller must look up the inner
    ``_ConditionFrame`` via ``ed._root_frame._child_frames[0]``.
    """
    parent.geometry(f"{_DIALOG_WIDTH_PX}x600")
    # Force the parent's actual width to update so the
    # picker's reflow has a real budget to work with.
    parent.update_idletasks()
    ed = BlockEditor(parent, root=Group(combinator="and", children=[cond]))
    ed.pack(fill="both", expand=True)
    parent.update_idletasks()
    # The reflow is debounced via `after_idle`; pump a few cycles so
    # the deferred layout fires.
    for _ in range(5):
        parent.update_idletasks()
        parent.update()
    return ed


def _rhs_picker(cf: _ConditionFrame) -> _FieldRefPicker | None:
    """Return the first FieldRef-typed RHS picker (``right`` / ``low``)."""
    for name in ("right", "low", "high", "target", "reference"):
        slot = cf._param_widgets.get(name)
        if slot and slot[0] == "field":
            return slot[1]
    return None


def _frame_right_edge(cf: _ConditionFrame) -> int:
    """Return the right-edge x-coordinate of the ConditionFrame in screen px."""
    return cf.winfo_rootx() + cf.winfo_width()


def _rhs_right_edge(cf: _ConditionFrame) -> int:
    """Return the right-edge of the RHS picker (rootx + reqwidth)."""
    picker = _rhs_picker(cf)
    assert picker is not None, "no RHS picker found for this cond"
    return picker.winfo_rootx() + picker.winfo_reqwidth()


# ---------------------------------------------------------------------------
# Simple inline case — sanity check that the helper machinery works.
# ---------------------------------------------------------------------------


def test_inline_close_gt_100_rhs_within_dialog_width(root):
    """``close > 100`` is the trivial case — RHS literal entry fits trivially."""
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = _make_sized_editor(root, c)
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "inline"
    picker = _rhs_picker(cf)
    assert picker is not None
    # The RHS picker must be left of (or at) the dialog's right edge.
    rhs_right = picker.winfo_rootx() + picker.winfo_reqwidth()
    dialog_right = root.winfo_rootx() + _DIALOG_WIDTH_PX
    assert rhs_right <= dialog_right, (
        f"RHS picker right edge {rhs_right} exceeds dialog right "
        f"edge {dialog_right} for trivial inline case"
    )


# ---------------------------------------------------------------------------
# Stacked layouts — the bug this design exists to fix.
# ---------------------------------------------------------------------------


def test_stacked_rvol_rhs_reachable(root):
    """RVOL on LEFT pushes the row to stacked; RHS picker sits on row 2.

    Pre-fix, the inline layout shoved the RHS off-canvas because
    RVOL's 6 params + indicator combo + symbol combo overflowed the
    half-row budget. In stacked mode the RHS sits on row 2 with the
    full row available, so winfo_rootx must be far left.
    """
    c = Condition(left=FieldRef.indicator("rvol"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = _make_sized_editor(root, c)
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "stacked"
    rhs = _rhs_picker(cf)
    assert rhs is not None
    rhs_right = rhs.winfo_rootx() + rhs.winfo_reqwidth()
    dialog_right = root.winfo_rootx() + _DIALOG_WIDTH_PX
    assert rhs_right <= dialog_right, (
        f"RVOL stacked: RHS picker right edge {rhs_right} exceeds "
        f"dialog right edge {dialog_right} (would be unreachable)"
    )
    # In stacked layout the RHS picker is below the LEFT picker on a
    # different grid row — assert that's actually the case to pin the
    # visual structure (not just the geometry).
    rhs_y = rhs.winfo_rooty()
    left_y = cf._left_picker.winfo_rooty()
    assert rhs_y > left_y, (
        f"RHS picker y={rhs_y} should be below LEFT picker y={left_y} "
        f"in stacked layout (instead they're on the same row)"
    )


def test_stacked_between_two_rhs_pickers_reachable(root):
    """BETWEEN op has two RHS pickers — both must remain reachable."""
    c = Condition(left=FieldRef.builtin("close"), op=OP_BETWEEN,
                  params={"low": FieldRef.literal(0.0),
                          "high": FieldRef.literal(10.0)},
                  interval="5m")
    ed = _make_sized_editor(root, c)
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "stacked"
    low = cf._param_widgets["low"][1]
    high = cf._param_widgets["high"][1]
    dialog_right = root.winfo_rootx() + _DIALOG_WIDTH_PX
    for label, picker in (("low", low), ("high", high)):
        right = picker.winfo_rootx() + picker.winfo_reqwidth()
        assert right <= dialog_right, (
            f"BETWEEN stacked: {label} picker right edge {right} "
            f"exceeds dialog right edge {dialog_right}"
        )
    # The two pickers stack vertically inside the fields frame.
    assert high.winfo_rooty() > low.winfo_rooty(), (
        "low / high pickers should stack vertically in stacked layout, "
        f"got low y={low.winfo_rooty()} high y={high.winfo_rooty()}"
    )


def test_stacked_cross_symbol_left_pin_rhs_reachable(root):
    """Cross-symbol pin doesn't auto-stack; verify at narrow width.

    Under the new fit-based classifier, a cross-symbol pin alone
    doesn't force stacked — but at a narrow dialog the overall
    inline width still overflows and triggers stacked. This test
    confirms RHS is reachable in that case.
    """
    c = Condition(left=FieldRef.builtin("close", symbol="SPY"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    # Narrow dialog: 800 px is below the inline estimate for a
    # cross-symbol-pinned condition (~1000+ px), so it stacks.
    narrow_width = 800
    root.geometry(f"{narrow_width}x600")
    root.deiconify()
    root.update_idletasks()
    root.update()
    ed = BlockEditor(root, root=Group(combinator="and", children=[c]))
    ed.pack(fill="x", padx=8, pady=8)
    root.update_idletasks()
    root.update()
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "stacked", (
        f"At {narrow_width} px, cross-symbol pin should stack, "
        f"got {cf._current_layout}"
    )
    rhs = _rhs_picker(cf)
    assert rhs is not None
    rhs_right = rhs.winfo_rootx() + rhs.winfo_reqwidth()
    dialog_right = root.winfo_rootx() + narrow_width
    assert rhs_right <= dialog_right


def test_stacked_complex_rhs_with_simple_left_reachable(root):
    """``close > rvol`` — complex RHS, simple LEFT. Row should stack."""
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.indicator("rvol")}, interval="5m")
    ed = _make_sized_editor(root, c)
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "stacked"
    rhs = _rhs_picker(cf)
    assert rhs is not None
    rhs_right = rhs.winfo_rootx() + rhs.winfo_reqwidth()
    dialog_right = root.winfo_rootx() + _DIALOG_WIDTH_PX
    assert rhs_right <= dialog_right


# ---------------------------------------------------------------------------
# Chrome widget reachability (interval combo + delete button)
# ---------------------------------------------------------------------------


def test_stacked_interval_and_delete_remain_on_row_0(root):
    """Stacked layout keeps interval + delete on row 0 (top-right corner)."""
    c = Condition(left=FieldRef.indicator("rvol"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = _make_sized_editor(root, c)
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "stacked"
    interval_y = cf._interval_combo.winfo_rooty()
    delete_y = cf._delete_btn.winfo_rooty()
    enabled_y = cf._enabled_chk.winfo_rooty()
    # All three are on row 0 of the outer grid.
    assert abs(interval_y - enabled_y) < 30, (
        "interval combo should share row 0 with enabled checkbox; "
        f"got interval_y={interval_y} enabled_y={enabled_y}"
    )
    assert abs(delete_y - enabled_y) < 30, (
        "delete button should share row 0 with enabled checkbox; "
        f"got delete_y={delete_y} enabled_y={enabled_y}"
    )
    # And both within the dialog's right edge.
    dialog_right = root.winfo_rootx() + _DIALOG_WIDTH_PX
    interval_right = (
        cf._interval_combo.winfo_rootx() + cf._interval_combo.winfo_reqwidth())
    delete_right = (
        cf._delete_btn.winfo_rootx() + cf._delete_btn.winfo_reqwidth())
    assert interval_right <= dialog_right
    assert delete_right <= dialog_right


# ---------------------------------------------------------------------------
# Layout-flip on the fly (regression for the dynamic rebuild path)
# ---------------------------------------------------------------------------


def test_op_change_inline_to_stacked_keeps_rhs_reachable(root):
    """Start inline (close > 100), flip op to between, RHS still reachable."""
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = _make_sized_editor(root, c)
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "inline"
    # Simulate the user picking BETWEEN.
    cf._op_var.set(OP_BETWEEN)
    cf._on_op_change()
    for _ in range(5):
        root.update_idletasks()
        root.update()
    assert cf._current_layout == "stacked"
    low = cf._param_widgets["low"][1]
    high = cf._param_widgets["high"][1]
    dialog_right = root.winfo_rootx() + _DIALOG_WIDTH_PX
    for label, picker in (("low", low), ("high", high)):
        right = picker.winfo_rootx() + picker.winfo_reqwidth()
        assert right <= dialog_right, (
            f"After op flip to BETWEEN: {label} picker right edge "
            f"{right} exceeds dialog right edge {dialog_right}"
        )


def test_left_change_to_rvol_then_back_to_close_lands_inline(root):
    """Round-trip: simple → complex → simple LEFT must collapse back to inline."""
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = _make_sized_editor(root, c)
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "inline"
    # Flip to rvol.
    cf._left_picker.set(FieldRef.indicator("rvol"))
    cf.cond.left = cf._left_picker.get()
    cf._on_left_change()
    assert cf._current_layout == "stacked"
    # Flip back to close.
    cf._left_picker.set(FieldRef.builtin("close"))
    cf.cond.left = cf._left_picker.get()
    cf._on_left_change()
    assert cf._current_layout == "inline"
    for _ in range(5):
        root.update_idletasks()
        root.update()
    rhs = _rhs_picker(cf)
    assert rhs is not None
    rhs_right = rhs.winfo_rootx() + rhs.winfo_reqwidth()
    dialog_right = root.winfo_rootx() + _DIALOG_WIDTH_PX
    assert rhs_right <= dialog_right


# ---------------------------------------------------------------------------
# on_change fires on layout flip (wheel-guard re-application contract)
# ---------------------------------------------------------------------------


def test_on_change_fires_when_left_picker_change_flips_layout(root):
    """CLAUDE.md §7.19: layout flip must propagate via ``on_change``.

    The consumer dialog (EntriesDialog / scanner tab) re-applies the
    wheel-guard inside ``on_change``; if a layout flip doesn't fire
    the callback, the freshly rebuilt comboboxes are wheel-bombable
    (CLAUDE.md §7.11).
    """
    fires: list[int] = []
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = BlockEditor(root, root=Group(combinator="and", children=[c]),
                     on_change=lambda: fires.append(1))
    cf = ed._root_frame._child_frames[0]
    # Triggering _on_left_change should fire on_change exactly once.
    cf._left_picker.set(FieldRef.indicator("rvol"))
    cf.cond.left = cf._left_picker.get()
    cf._on_left_change()
    assert len(fires) >= 1, "left-change layout flip must fire on_change"


def test_on_change_fires_when_op_change_flips_layout(root):
    fires: list[int] = []
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = BlockEditor(root, root=Group(combinator="and", children=[c]),
                     on_change=lambda: fires.append(1))
    cf = ed._root_frame._child_frames[0]
    cf._op_var.set(OP_BETWEEN)
    cf._on_op_change()
    assert len(fires) >= 1


def test_block_editor_exposes_condition_view_toggle(root):
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = BlockEditor(root, root=Group(combinator="and", children=[c]))
    values = tuple(ed._view_combo.cget("values"))
    assert values == ("Auto layout", "Compact rows", "Detailed cards")
    assert ed._view_var.get() == "Auto layout"


def test_detailed_cards_forces_stacked_layout(root):
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = BlockEditor(root, root=Group(combinator="and", children=[c]))
    ed.set_view_mode("Detailed cards")
    cf = ed._root_frame._child_frames[0]
    assert cf._current_layout == "stacked"


def test_compact_rows_show_trader_summary(root):
    c = Condition(left=FieldRef.builtin("close", symbol="SPY"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    ed = BlockEditor(root, root=Group(combinator="and", children=[c]))
    ed.set_view_mode("Compact rows")
    cf = ed._root_frame._child_frames[0]
    assert cf._summary_label is not None
    summary = cf._summary_label.cget("text")
    assert "SPY" in summary
    assert ">" in summary
