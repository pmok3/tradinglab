# gui/exits_dialog_widgets.py — Spec

## Purpose

Re-usable Tk widgets composing the `ExitsDialog` editor pane:
field-spec utilities, a small bracket-quick-dialog, and the leg /
trigger / OCO row widgets. Keeps the dialog focused on
orchestration; widgets focus on rendering one schema element.

## Widget catalogue

```python
# _FieldSpec is imported from gui._trigger_field_renderer.
# See _trigger_field_renderer.spec.md for the kind taxonomy.

class _BracketDialog(BaseModalDialog):
    """One-shot modal: prompts for target/stop unit+value, qty%,
    and name; exposes a result dict consumed by make_bracket_strategy."""

class _LegFrame(ttk.LabelFrame):
    """Renders and mutates one ExitLeg: label, enabled flag,
    trigger rows, add/delete-trigger controls, and delete-leg button."""
    def __init__(self, master, *, leg: ExitLeg, dialog: "ExitsDialog")
    @property
    def leg(self) -> ExitLeg
    def remove_trigger(trigger_id: str) -> None

class _TriggerRow(ttk.Frame):
    """Single ExitTrigger row: kind dropdown + qty%, enabled, label,
    kind-specific subform, and delete button. INDICATOR triggers embed
    a BlockEditor."""
    def __init__(self, master, *, trigger: ExitTrigger, leg_frame: _LegFrame)
    @property
    def trigger(self) -> ExitTrigger
    @property
    def block_editor(self) -> BlockEditor | None

class _OCOGroupRow(ttk.Frame):
    """OCO-group editor row: leg chips, cancel_on dropdown, and
    delete-group button. Duplicate leg membership is highlighted."""
```

## Dependencies

- `..exits.model.{ExitLeg, ExitTrigger, TriggerKind, ...}`.
- `.scanner_block_editor.BlockEditor` for INDICATOR trigger
  condition trees.
- `._trigger_field_renderer` for `_FieldSpec` and field rendering.
- `..exits.spec` is **not** imported (widgets stay schema-level).

## Design Decisions

- **`_FieldSpec` is the declarative seam**: adding a new operator
  param is a one-line addition to a `_FieldSpec` list. Rendering and
  parse callbacks flow through `gui._trigger_field_renderer`, keeping
  layout + parse + format in lockstep.
- **`_on_kind_changed` is idempotent (flicker fix).** It short-circuits
  when the resolved kind equals `_trigger.kind`, so re-picking the
  current kind — or a spurious combobox event — does NOT tear down +
  rebuild the per-kind param subform (the "window flickers when I touch
  the dropdown" bug). A genuine kind change still rebuilds. Pinned by
  `tests/unit/gui/test_dialog_combobox_no_flicker.py`.
- **Renderer lifted to `gui._trigger_field_renderer` (audit #8).**
  ``_FieldSpec`` and the per-kind widget construction (formerly
  ``_TriggerRow._render_field``) now live in
  ``gui/_trigger_field_renderer.py`` so the entries-side dialog
  can drive its own ``_ENTRY_TRIGGER_SPECS`` through the same
  primitive. The local ``_render_field`` is a thin delegator that
  bridges the shared renderer's ``get_value`` / ``on_change``
  callbacks to ``getattr``/``setattr`` on ``self._trigger`` and
  stashes the returned ``tk.Variable`` in ``self._param_vars``.
  The exits-side ``_FIELD_SPECS_BY_KIND`` registry stays here
  (it is exits-specific). The kind taxonomy is documented in
  [`_trigger_field_renderer.spec.md`](_trigger_field_renderer.spec.md).
- **`threshold_warn` / `threshold_extreme` removed** from RVOL /
  RRVOL operator subforms (cosmetic chart-overlay reference lines
  only, never read by any trigger evaluation path). Still on the
  underlying dataclasses for backward-compatible JSON loads.
- **One row, one trigger**. Combining triggers into a leg is the
  dialog's job.
- **Widgets mutate the draft in place.** The dialog owns a cloned
  `ExitStrategy` draft; leg, trigger, and OCO widgets update that draft
  directly, and Cancel stays safe because the clone is never written
  until Save.

## Invariants

- Widget `__init__` runs only on Tk thread.
- Numeric parse failures are ignored while the user is typing; final validation is dialog-owned.
- Widgets only mutate the dialog draft, never the saved library directly.

## See also

- Owner: [`exits_dialog.spec.md`](exits_dialog.spec.md).
- Schema: [`../exits/model.spec.md`](../exits/model.spec.md).
- Indicator editor:
  [`scanner_block_editor.spec.md`](scanner_block_editor.spec.md).
