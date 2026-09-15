# gui/flow_layout.py - Spec

Last updated: 2026-09-15

## Purpose

Opt-in measured wrapping for compact action rows that otherwise lose controls
when their pane narrows or UI fonts grow. Does not alter the main toolbar.

## Public API

- `wrap_controls(container, *, gap=4) -> FlowLayout` captures the currently
  packed controls in packing order and installs a resize-reactive layout.
- `FlowLayout` retains the container, controls and owned idle/binding state.

## Behavior

Controls remain children of their original container. Packing them into child
row frames with `in_` preserves widget identity, callbacks, focus and tab order.
The helper greedily wraps their actual requested widths against the allocated
container width. Rows reuse their frames and controls are never reconstructed.

Only controls whose geometry manager is still `pack` participate. An initially
hidden control is not captured; a captured control hidden with `pack_forget`
remains hidden on subsequent resizes. Re-packing it makes it eligible again.
Disabled state is never changed.

Configure events coalesce through one owned idle callback. Identical width/
control-request signatures do not repack. Destroy cancels that callback and
removes the helper's Configure bindings without removing unrelated bindings.

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
