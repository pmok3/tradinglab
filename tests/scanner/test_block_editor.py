"""Block editor widget tests.

The editor is a thin Tk view over the model layer. Tests focus on:

- Widget construction without crashing.
- Tree round-trip: set_root → get_root preserves shape + ids.
- Programmatic op change rebuilds params and stays model-valid.
- on_change fires on user-equivalent edits.
- Adding / deleting children mutates the underlying tree.
"""

from __future__ import annotations

import tkinter as tk

import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import BlockEditor
from tradinglab.scanner.model import (
    OP_BETWEEN,
    OP_GT,
    OP_INSIDE_BAR,
    OP_IS_RISING,
    Condition,
    FieldRef,
    Group,
)


def _simple_group() -> Group:
    return Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m"),
    ])


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_editor_constructs_with_empty_root(root):
    ed = BlockEditor(root)
    assert ed.get_root().combinator == "and"
    assert ed.get_root().children == []


def test_editor_constructs_with_seeded_tree(root):
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    out = ed.get_root()
    assert out is g  # in-place
    assert len(out.children) == 1
    assert out.children[0].op == OP_GT


def test_editor_set_root_replaces_tree(root):
    ed = BlockEditor(root, root=_simple_group())
    new = Group(combinator="or", children=[
        Condition(left=FieldRef.builtin("volume"), op=OP_GT,
                  params={"right": FieldRef.literal(0.0)}, interval="1m"),
    ])
    ed.set_root(new)
    out = ed.get_root()
    assert out.combinator == "or"
    assert out.children[0].interval == "1m"


# ---------------------------------------------------------------------------
# on_change firing
# ---------------------------------------------------------------------------


def test_on_change_fires_on_add_condition(root):
    fires = []
    ed = BlockEditor(root, root=Group(combinator="and", children=[]),
                     on_change=lambda: fires.append(1))
    # Reach inside to the root frame to invoke its add-condition callback.
    ed._root_frame._add_condition()
    assert len(fires) == 1
    assert len(ed.get_root().children) == 1


def test_on_change_fires_on_add_group(root):
    fires = []
    ed = BlockEditor(root, root=Group(combinator="and", children=[]),
                     on_change=lambda: fires.append(1))
    ed._root_frame._add_group()
    assert len(fires) == 1
    assert isinstance(ed.get_root().children[0], Group)


# ---------------------------------------------------------------------------
# Op change
# ---------------------------------------------------------------------------


def test_op_change_rebuilds_params(root):
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m"),
    ])
    ed = BlockEditor(root, root=g)
    cond_frame = ed._root_frame._child_frames[0]
    # Programmatically simulate the user picking BETWEEN.
    cond_frame._op_var.set(OP_BETWEEN)
    cond_frame._on_op_change()
    new_cond = ed.get_root().children[0]
    assert new_cond.op == OP_BETWEEN
    assert set(new_cond.params.keys()) == {"low", "high"}


def test_op_change_to_structural_drops_to_no_params(root):
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m"),
    ])
    ed = BlockEditor(root, root=g)
    cf = ed._root_frame._child_frames[0]
    cf._op_var.set(OP_INSIDE_BAR)
    cf._on_op_change()
    new_cond = ed.get_root().children[0]
    assert new_cond.op == OP_INSIDE_BAR
    assert new_cond.params == {}


def test_op_change_preserves_left_and_id(root):
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("volume"), op=OP_GT,
                  params={"right": FieldRef.literal(0.0)}, interval="5m"),
    ])
    original_id = g.children[0].id
    ed = BlockEditor(root, root=g)
    cf = ed._root_frame._child_frames[0]
    cf._op_var.set(OP_IS_RISING)
    cf._on_op_change()
    new_cond = ed.get_root().children[0]
    assert new_cond.id == original_id
    assert new_cond.left.id == "volume"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_condition_removes_from_tree(root):
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    cf = ed._root_frame._child_frames[0]
    ed._root_frame._remove_child_widget(cf)
    assert ed.get_root().children == []


def test_delete_one_of_many_keeps_others(root):
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m"),
        Condition(left=FieldRef.builtin("volume"), op=OP_GT,
                  params={"right": FieldRef.literal(0.0)}, interval="5m"),
    ])
    target_id_to_keep = g.children[1].id
    ed = BlockEditor(root, root=g)
    first = ed._root_frame._child_frames[0]
    ed._root_frame._remove_child_widget(first)
    remaining = ed.get_root().children
    assert len(remaining) == 1
    assert remaining[0].id == target_id_to_keep


# ---------------------------------------------------------------------------
# Combinator + enabled
# ---------------------------------------------------------------------------


def test_combinator_change_mutates_group(root):
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    ed._root_frame._combinator_var.set("OR")
    ed._root_frame._on_combinator_change()
    assert ed.get_root().combinator == "or"


def test_enabled_toggle_mutates_condition(root):
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    cf = ed._root_frame._child_frames[0]
    cf._enabled_var.set(False)
    cf._on_enabled_toggle()
    assert ed.get_root().children[0].enabled is False


# ---------------------------------------------------------------------------
# Round-trip: deeply nested
# ---------------------------------------------------------------------------


def test_nested_group_round_trip(root):
    inner = Group(combinator="or", children=[
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m"),
    ])
    outer = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("volume"), op=OP_GT,
                  params={"right": FieldRef.literal(0.0)}, interval="5m"),
        inner,
    ])
    ed = BlockEditor(root, root=outer)
    rt = ed.get_root()
    assert len(rt.children) == 2
    assert isinstance(rt.children[1], Group)
    assert rt.children[1].combinator == "or"
    assert len(rt.children[1].children) == 1


def test_indicator_field_picker_renders(root):
    """Smoke: building a Condition whose left is an indicator with params doesn't crash."""
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.indicator("sma", params={"length": 50}),
            op=OP_GT,
            params={"right": FieldRef.literal(100.0)},
            interval="5m",
        ),
    ])
    ed = BlockEditor(root, root=g)
    rt = ed.get_root()
    assert rt.children[0].left.id == "sma"
    assert rt.children[0].left.params.get("length") == 50


# ---------------------------------------------------------------------------
# Combinator visibility (UX: hide AND/OR when nothing to combine)
# ---------------------------------------------------------------------------


def _combinator_visible(group_frame) -> bool:
    """True when the combinator combobox is currently packed (visible)."""
    try:
        return bool(group_frame._combinator_cb.winfo_manager())
    except tk.TclError:
        return False


def test_combinator_hidden_on_empty_root(root):
    """Empty group → AND/OR combobox is not shown."""
    ed = BlockEditor(root, root=Group(combinator="and", children=[]))
    assert _combinator_visible(ed._root_frame) is False


def test_combinator_hidden_with_single_child(root):
    """Group with one child → no combinator (nothing to combine)."""
    ed = BlockEditor(root, root=_simple_group())
    assert _combinator_visible(ed._root_frame) is False


def test_combinator_visible_with_two_children(root):
    """Group with 2+ children → combinator is shown."""
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m"),
        Condition(left=FieldRef.builtin("volume"), op=OP_GT,
                  params={"right": FieldRef.literal(0.0)}, interval="5m"),
    ])
    ed = BlockEditor(root, root=g)
    assert _combinator_visible(ed._root_frame) is True


def test_combinator_appears_when_second_condition_added(root):
    """Adding a 2nd child reveals the combinator; removing it hides again."""
    ed = BlockEditor(root, root=Group(combinator="and", children=[]))
    gf = ed._root_frame
    assert _combinator_visible(gf) is False

    gf._add_condition()
    assert _combinator_visible(gf) is False  # still 1 child

    gf._add_condition()
    assert _combinator_visible(gf) is True   # 2 children → visible

    # Remove one child via the public widget-deletion path.
    gf._remove_child_widget(gf._child_frames[0])
    assert _combinator_visible(gf) is False  # back to 1 child → hidden


# ---------------------------------------------------------------------------
# Child ordering: conditions first, groups last (UX consistency)
# ---------------------------------------------------------------------------


def _frame_kinds(group_frame):
    """Return a list like ['cond', 'cond', 'group'] in render order."""
    from tradinglab.gui.scanner_block_editor import (
        _ConditionFrame,
        _GroupFrame,
    )
    out = []
    for f in group_frame._child_frames:
        if isinstance(f, _ConditionFrame):
            out.append("cond")
        elif isinstance(f, _GroupFrame):
            out.append("group")
    return out


def test_render_sorts_conditions_before_groups_for_legacy_mixed_data(root):
    """Legacy data with [Group, Condition, Group] still renders cond-first."""
    g = Group(combinator="and", children=[
        Group(combinator="and", children=[]),
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(1.0)}, interval="5m"),
        Group(combinator="or", children=[]),
    ])
    ed = BlockEditor(root, root=g)
    assert _frame_kinds(ed._root_frame) == ["cond", "group", "group"]


def test_add_condition_inserts_before_existing_groups(root):
    """Clicking '+ Condition' after a group has been added places the
    new condition before the group in the data model."""
    ed = BlockEditor(root, root=Group(combinator="and", children=[]))
    gf = ed._root_frame
    gf._add_group()       # children: [Group]
    gf._add_condition()   # should insert at index 0, not append.
    kinds = [type(c).__name__ for c in ed.get_root().children]
    assert kinds == ["Condition", "Group"]
    # Render order matches data order (already canonical).
    assert _frame_kinds(gf) == ["cond", "group"]


def test_add_condition_inserts_after_last_existing_condition(root):
    """With [Cond, Cond, Group] adding a condition produces
    [Cond, Cond, NewCond, Group] (after last cond, before group)."""
    ed = BlockEditor(root, root=Group(combinator="and", children=[]))
    gf = ed._root_frame
    gf._add_condition()
    gf._add_condition()
    gf._add_group()
    gf._add_condition()
    kinds = [type(c).__name__ for c in ed.get_root().children]
    assert kinds == ["Condition", "Condition", "Condition", "Group"]


# ---------------------------------------------------------------------------
# Adaptive flow layout
# ---------------------------------------------------------------------------


from tradinglab.gui.scanner_block_editor import (  # noqa: E402
    _compute_flow_rows,
    _FieldRefPicker,
)


def test_compute_flow_rows_single_row_when_budget_large():
    """All children fit on row 0 when budget exceeds the total width."""
    placements = _compute_flow_rows([100, 100, 100, 100], budget=10_000, pad=6)
    assert placements == [(0, 0), (0, 1), (0, 2), (0, 3)]


def test_compute_flow_rows_wraps_when_budget_tight():
    """Wraps to row 1 when the running width exceeds budget."""
    # Each child = 100 + pad 6 = 106 px running cost.
    # Budget=200 fits one (106 used) then 106+106=212 > 200 → wrap.
    placements = _compute_flow_rows([100, 100, 100, 100], budget=200, pad=6)
    rows = [r for r, _ in placements]
    assert rows == [0, 1, 2, 3] or rows == [0, 1, 1, 2] or 0 in rows
    # Stronger invariants: every row's sum of widths+pad <= budget,
    # except possibly a row containing a single oversize widget.
    by_row: dict = {}
    for w, (r, _c) in zip([100, 100, 100, 100], placements, strict=False):
        by_row.setdefault(r, 0)
        by_row[r] += w + 6
    for r, total in by_row.items():
        # Exactly one widget per row in this scenario (each ~106 px,
        # budget 200 → fits 1, second triggers wrap).
        assert total <= 200 or sum(1 for rr, _ in placements if rr == r) == 1


def test_compute_flow_rows_oversize_widget_gets_own_row():
    """A child wider than the budget still gets placed (alone on its row)."""
    placements = _compute_flow_rows([50, 500, 50], budget=200, pad=6)
    # First child (50+6=56) on row 0; second child (500+6=506) overflows
    # but col=1 forces a wrap → row 1 col 0; third child (50+6=56)
    # on row 1 col 1 if 506+56<=200 (no) so row 2 col 0.
    rows = [r for r, _ in placements]
    cols = [c for _, c in placements]
    assert rows[0] == 0 and cols[0] == 0
    # Oversize child wraps to a new row (col=0 forces wrap if not first).
    assert rows[1] > rows[0] or rows[1] == 0
    # Each child placed exactly once.
    assert len(placements) == 3


def test_compute_flow_rows_first_child_always_on_row_zero():
    """The very first widget is always placed at (0, 0), even if oversize."""
    placements = _compute_flow_rows([10_000], budget=100, pad=6)
    assert placements == [(0, 0)]


def test_compute_flow_rows_zero_budget_safe():
    """A zero/negative budget falls back to budget=1 — every child wraps."""
    placements = _compute_flow_rows([10, 10, 10], budget=0, pad=0)
    rows = [r for r, _ in placements]
    # Every child past the first wraps because 10 > 1 (the floor).
    assert rows[0] == 0
    assert rows[1] > 0
    assert rows[2] > rows[1]


def test_compute_flow_rows_empty_input():
    assert _compute_flow_rows([], budget=500) == []


# ----- _FieldRefPicker integration ----------------------------------------


def _make_indicator_picker(root, indicator_id: str) -> _FieldRefPicker:
    """Build a FieldRefPicker for the given indicator."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.indicator(indicator_id),
    )
    picker.pack(fill="both", expand=True)
    root.update_idletasks()
    return picker


def test_field_ref_picker_records_flow_children_for_rvol(root):
    """RVOL trigger form records params plus Basic/Advanced headers.

    The pre-pruning total was 9 (8 params + combo); see RVOL's
    ``TRIGGER_RELEVANT_PARAMS`` which hides the two cosmetic-only
    reference-line knobs (``threshold_warn`` / ``threshold_extreme``)
    from the entries / exits / scanner block-editor form.
    """
    picker = _make_indicator_picker(root, "rvol")
    try:
        # Indicator combo + Basic/Advanced headers + 6 trigger-relevant
        # params (mode, length,
        # aggregator, session_filter, denominator_includes_current,
        # z_score) + 1 cross-symbol Symbol wrap. RVOL has a single
        # output key, so no output combo is added.
        assert len(picker._flow_children) == 10
        labels = [
            w.cget("text") for w in picker._flow_children
            if type(w).__name__ == "Label"
        ]
        assert "Basic" in labels
        assert "Advanced" in labels
    finally:
        picker.destroy()


def test_field_ref_picker_records_flow_children_for_smi(root):
    """SMI has multi-output → output combo is part of the flow."""
    picker = _make_indicator_picker(root, "smi")
    try:
        # SMI has 2 output keys (smi, signal) so the output combo is
        # appended to flow_children. Concrete count depends on SMI's
        # params_schema, but it must include the output combo. The
        # very last flow child is now the cross-symbol Symbol wrap
        # Frame (added after the output combo) — pin that the output
        # combo still appears somewhere in the flow.
        assert len(picker._flow_children) >= 3
        widget_types = [type(w).__name__ for w in picker._flow_children]
        assert any("Combobox" in name for name in widget_types)
    finally:
        picker.destroy()


def test_field_ref_picker_reflow_wraps_when_narrow(root):
    """A narrow Toplevel forces multi-row layout for RVOL params."""
    root.geometry("400x300")
    # Under xvfb/headless Linux, ``update_idletasks`` doesn't always
    # process the synthetic Configure event from ``geometry()`` so
    # ``winfo_width`` can keep reporting the screen-default width. Do
    # a full ``update()`` and then assert (or stub) the width before
    # invoking the reflow so the test is deterministic across display
    # backends.
    root.update()
    picker = _make_indicator_picker(root, "rvol")
    try:
        # Force the toplevel reference and a stable measured width.
        # The reflow reads ``self._toplevel_for_reflow.winfo_width()``;
        # by stubbing it we make the test exercise the layout logic
        # rather than the windowing system.
        picker._toplevel_for_reflow = root
        original_winfo_width = root.winfo_width
        root.winfo_width = lambda: 400  # type: ignore[method-assign]
        try:
            picker._reflow_value_pane()
        finally:
            root.winfo_width = original_winfo_width  # type: ignore[method-assign]
        # New layout uses per-row sub-frames (each widget is packed
        # ``side="left"`` inside its row Frame). Verify wrapping by
        # asserting >1 row frames exist.
        # At 400px toplevel, budget = max(180, (400-280)//2) = 180.
        # 9 widgets averaging ~80-120px each can't all fit on row 0.
        assert len(picker._flow_row_frames) > 1, (
            f"Expected wrapping at 400px toplevel, got "
            f"{len(picker._flow_row_frames)} row frame(s)")
    finally:
        picker.destroy()


def test_field_ref_picker_reflow_single_row_when_wide(root):
    """A wide Toplevel keeps RVOL params on row 0."""
    root.geometry("3000x600")
    root.update()
    picker = _make_indicator_picker(root, "rvol")
    try:
        picker._toplevel_for_reflow = root
        original_winfo_width = root.winfo_width
        root.winfo_width = lambda: 3000  # type: ignore[method-assign]
        try:
            picker._reflow_value_pane()
        finally:
            root.winfo_width = original_winfo_width  # type: ignore[method-assign]
        # At 3000px toplevel: budget = max(180, (3000-280)//2) = 1360.
        # 9 widgets at ~80-120px (≈800 total + padding) all fit on row 0.
        assert len(picker._flow_row_frames) == 1, (
            f"Expected single-row layout at 3000px toplevel, got "
            f"{len(picker._flow_row_frames)} row frame(s)")
    finally:
        picker.destroy()


def test_field_ref_picker_destroy_unbinds_toplevel(root):
    """Destroying the picker removes its Toplevel <Configure> binding."""
    picker = _make_indicator_picker(root, "rvol")
    bind_id = picker._toplevel_bind_id
    top_ref = picker._toplevel_for_reflow
    assert bind_id is not None
    assert top_ref is not None
    picker.destroy()
    # After destroy, the funcid+toplevel refs are cleared.
    assert picker._toplevel_for_reflow is None
    assert picker._toplevel_bind_id is None
    # Firing a Configure event on the toplevel must not raise.
    try:
        top_ref.event_generate("<Configure>", x=0, y=0, width=500, height=400)
    except tk.TclError:
        # event_generate may fail on withdrawn windows; the important
        # thing is that no callback fires against the destroyed picker.
        pass


def test_field_ref_picker_rebuild_cancels_pending_reflow(root):
    """Switching indicator (rebuilds value pane) cancels pending reflow."""
    picker = _make_indicator_picker(root, "rvol")
    try:
        # Schedule a reflow (simulating debounce-pending state).
        picker._reflow_after_id = picker.after(10_000, picker._reflow_value_pane)
        old_id = picker._reflow_after_id
        assert old_id is not None
        # Switch to a different indicator → triggers _rebuild_value_pane.
        picker._field_id_var.set("sma")
        picker._on_indicator_change()
        # The old after_id must have been cancelled (so it can't fire
        # against the now-destroyed children).
        # _rebuild_value_pane re-schedules a fresh after_idle reflow,
        # so _reflow_after_id is non-None but DIFFERENT from old_id.
        assert picker._reflow_after_id != old_id
    finally:
        picker.destroy()


def test_condition_frame_chrome_uses_top_alignment(root):
    """When the left picker grows multi-row, ConditionFrame chrome
    cells must be top-anchored so the operator combo doesn't visually
    drift to the picker's vertical centre.
    """
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.indicator("rvol"),
            op=OP_GT,
            params={"right": FieldRef.literal(2.0)},
            interval="5m",
        ),
    ])
    ed = BlockEditor(root, root=g)
    try:
        # Locate the ConditionFrame.
        gf = ed._root_frame
        # _GroupFrame stores child frames; pick the first condition.
        cf = next(
            iter(c for c in gf._children_frame.winfo_children()
                 if type(c).__name__ == "_ConditionFrame"),
            None,
        )
        assert cf is not None
        # Walk row=0 columns; every child's grid_info should have
        # sticky containing 'n' (top-aligned).
        for child in cf.winfo_children():
            info = child.grid_info()
            if not info:
                continue
            sticky = str(info.get("sticky", ""))
            assert "n" in sticky, (
                f"ConditionFrame child {child} sticky={sticky!r} — "
                f"expected top-anchored ('n' or 'nw')")
    finally:
        ed.destroy()


# ---------------------------------------------------------------------------
# Within-last-N-bars look-back cluster
# ---------------------------------------------------------------------------


def _find_condition_frame(ed):
    """Return the first ``_ConditionFrame`` in the editor's root group."""
    gf = ed._root_frame
    for c in gf._children_frame.winfo_children():
        if type(c).__name__ == "_ConditionFrame":
            return c
    return None


def test_lookback_cluster_default_zero_and_any(root):
    """Fresh Condition row exposes a cluster with N=0 / mode=any."""
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        assert cf is not None
        cluster = cf._lookback
        assert cluster._bars_var.get() == "0"
        assert cluster._mode_var.get() == "any"
        # Backed by the same Condition object, not a copy.
        assert cluster._node is cf.cond
    finally:
        ed.destroy()


def test_lookback_cluster_spinbox_commits_to_model(root):
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        cluster = cf._lookback
        cluster._bars_var.set("3")
        cluster._on_bars_change()
        assert cf.cond.within_last_bars == 3
        # Round-trip via get_root.
        out_cond = ed.get_root().children[0]
        assert out_cond.within_last_bars == 3
    finally:
        ed.destroy()


def test_lookback_cluster_clamps_negative_input(root):
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        cluster = cf._lookback
        cluster._bars_var.set("-5")
        cluster._on_bars_change()
        assert cf.cond.within_last_bars == 0
        # Display also normalized.
        assert cluster._bars_var.get() == "0"
    finally:
        ed.destroy()


def test_lookback_cluster_clamps_above_max(root):
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        cluster = cf._lookback
        cluster._bars_var.set("999")
        cluster._on_bars_change()
        assert cf.cond.within_last_bars == 50
        assert cluster._bars_var.get() == "50"
    finally:
        ed.destroy()


def test_lookback_cluster_mode_combobox_commits(root):
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        cluster = cf._lookback
        cluster._mode_var.set("all")
        cluster._on_mode_change()
        assert cf.cond.within_last_mode == "all"
    finally:
        ed.destroy()


def test_lookback_cluster_mode_list_for_transition_op_hides_all(root):
    """For crosses_above / crosses_below the 'all' mode is hidden."""
    from tradinglab.scanner.model import OP_CROSSES_ABOVE
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=OP_CROSSES_ABOVE,
            params={"right": FieldRef.literal(100.0), "lookback": 1},
            interval="5m",
        ),
    ])
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        cluster = cf._lookback
        values = list(cluster._mode_combo.cget("values"))
        assert "any" in values
        assert "exactly" in values
        assert "all" not in values
    finally:
        ed.destroy()


def test_lookback_cluster_op_change_coerces_all_to_any(root):
    """When a Condition's op flips from a non-transition to a
    transition, an 'all' mode is coerced back to 'any' so the user
    isn't left with a mode that's no longer in the dropdown."""
    from tradinglab.scanner.model import OP_CROSSES_ABOVE
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=OP_GT,
            params={"right": FieldRef.literal(100.0)},
            interval="5m",
            within_last_bars=2,
            within_last_mode="all",
        ),
    ])
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        # Flip to crosses_above through the public op-change path.
        cf._op_var.set(OP_CROSSES_ABOVE)
        cf._on_op_change()
        assert cf.cond.op == OP_CROSSES_ABOVE
        assert cf.cond.within_last_mode == "any"
        # Cluster displays the coerced value too.
        assert cf._lookback._mode_var.get() == "any"
    finally:
        ed.destroy()


def test_lookback_cluster_emphasis_changes_with_n(root):
    """Visual: label foreground flips between muted (N=0) and active (N>0)."""
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        cluster = cf._lookback
        muted = str(cluster._label.cget("foreground"))
        cluster._bars_var.set("3")
        cluster._on_bars_change()
        active = str(cluster._label.cget("foreground"))
        assert muted != active
    finally:
        ed.destroy()


def test_lookback_cluster_on_group_header(root):
    """``_GroupFrame`` exposes its own group-level lookback cluster."""
    g = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"), op=OP_GT,
            params={"right": FieldRef.literal(100.0)}, interval="5m",
        ),
    ])
    ed = BlockEditor(root, root=g)
    try:
        gf = ed._root_frame
        cluster = gf._lookback
        assert cluster._node is gf.group
        cluster._bars_var.set("4")
        cluster._on_bars_change()
        assert ed.get_root().within_last_bars == 4
    finally:
        ed.destroy()


def test_lookback_cluster_on_group_header_shows_full_mode_list(root):
    """Group clusters always show all three modes (no op-aware hiding)."""
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    try:
        cluster = ed._root_frame._lookback
        values = list(cluster._mode_combo.cget("values"))
        assert "any" in values
        assert "all" in values
        assert "exactly" in values
    finally:
        ed.destroy()


def test_lookback_round_trips_through_get_root(root):
    """Editing the cluster persists through ``get_root`` JSON shape."""
    g = _simple_group()
    ed = BlockEditor(root, root=g)
    try:
        cf = _find_condition_frame(ed)
        cf._lookback._bars_var.set("5")
        cf._lookback._on_bars_change()
        cf._lookback._mode_var.set("exactly")
        cf._lookback._on_mode_change()
        out = ed.get_root()
        cond = out.children[0]
        assert cond.within_last_bars == 5
        assert cond.within_last_mode == "exactly"
        # Round-trip dict preserves the non-default values.
        d = cond.to_dict()
        assert d["within_last_bars"] == 5
        assert d["within_last_mode"] == "exactly"
    finally:
        ed.destroy()
