# gui/flow_layout.py - Spec

Last updated: 2026-09-15

## Purpose

Opt-in measured wrapping for compact action rows that otherwise lose controls
when their pane narrows or UI fonts grow. Does not alter the main toolbar.

## Public API

- `wrap_controls(container, *, controls=None, gap=4) -> FlowLayout` captures the
  currently packed controls in packing order, or an explicit ordered sequence
  including initially hidden controls, and installs a resize-reactive layout.
- `FlowLayout` retains the container, controls and owned idle/binding state.

## Behavior

Controls remain children of their original container. Packing them into child
row frames with `in_` preserves widget identity, callbacks, focus and tab order.
The helper greedily wraps their actual requested widths against the allocated
row width (inside container padding). Rows reuse their frames and controls are
never reconstructed. This is for compact action rows: per-child pack alignment
and external padding become left-aligned rows with the explicit uniform gap.
Callers supply logical order explicitly when their original right-packing order
differs from the desired reading order. Do not apply it to arbitrary forms.

Only controls whose geometry manager is still `pack` participate. An initially
hidden control is not captured; a captured control hidden with `pack_forget`
remains hidden on subsequent resizes. Re-packing it makes it eligible again.
With an explicit `controls` sequence, initially hidden items also become eligible
when first shown; they are never automatically shown by resize.
Disabled state is never changed.

Configure events coalesce through one owned idle callback. Identical width/
control-request signatures do not repack. Destroy cancels that callback and
removes the helper's Configure bindings without removing unrelated bindings.
Reflow waits until the container is viewable (all of its ancestors mapped).
Canvas window children can report mapped despite a withdrawn Toplevel or
unselected notebook page; their provisional width must not drive wrapping
and parent natural-size requests back and forth. Before mapping, original packed controls retain their natural
requests for opt-in form measurement. Map events resume the same coalesced
callback even without a resize, and their bindings are removed on destroy.
The Toplevel's Map event is included: an embedded child's mapped flag may
already be set and therefore cannot be the only wake-up signal. Destroying
the row removes that top-level binding too, preserving unrelated bindings.

## Invariants

- Importing the module changes no widgets; callers explicitly opt in.
- A single control wider than the available row is not silently shrunk; its
  owner must provide an appropriate minimum, responsive control or scrolling.
- Hidden controls are not resurrected to satisfy a width test.
- No settings, persistence, network work or global bindings.

## Testing

`tests/unit/gui/test_flow_layout.py` covers shrink/grow, enlarged fonts, retained
identity and disabled state, hidden controls, teardown and non-opted-in rows.
Application cases use the same mapped width checker as standard dialogs.
`test_window_width_rebuilds.py` exercises the legacy d81 EntriesDialog RVOL
rebuild/classification path while withdrawn, then maps, shrinks and grows it.
The unchanged `check_d81_rvol_rhs_reachable` smoke check must finish without
idle geometry feedback; its normal timeout is not relaxed.
