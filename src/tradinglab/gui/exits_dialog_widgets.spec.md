# gui/exits_dialog_widgets.py — Spec

## Purpose

Re-usable Tk widgets composing the `ExitsDialog` editor pane:
field-spec utilities, a small bracket-quick-dialog, and the leg /
trigger / OCO row widgets. Keeps the dialog focused on
orchestration; widgets focus on rendering one schema element.

## Widget catalogue

```python
# _FieldSpec is now imported from gui._trigger_field_renderer
# (audit item #8 lift). Its fields:
#   attr / label / kind / width / choices / separator
# See _trigger_field_renderer.spec.md for the kind taxonomy.

class _BracketDialog(tk.Toplevel):
    """One-shot modal: prompts for (target_pct, stop_pct, qty),
    invokes make_bracket_strategy on OK."""

class _LegFrame(ttk.LabelFrame):
    """Renders one ExitLeg: triggers (rows of _TriggerRow),
    OCO group dropdown (_OCOGroupRow), remove-leg button."""
    def __init__(self, master, *, leg: ExitLeg, dialog: "ExitsDialog")
    def collect(self) -> ExitLeg
    def validate(self) -> List[str]

class _TriggerRow(ttk.Frame):
    """Single ExitTrigger row: kind dropdown + kind-specific subform
    (price, stop_price, condition, atr_period, atr_multiple, …).
    INDICATOR triggers embed a _BlockEditor."""
    def __init__(self, master, *, trigger: ExitTrigger, leg_frame: _LegFrame)
    def collect(self) -> ExitTrigger
    def validate(self) -> List[str]

class _OCOGroupRow(ttk.Frame):
    """Per-leg OCO-group picker (None / A / B / …). Two legs in
    the same group cancel each other on fire."""
```

## Dependencies

- `..exits.model.{ExitLeg, ExitTrigger, TriggerKind, …}`.
- `.scanner_block_editor.BlockEditor` for INDICATOR trigger
  condition trees.
- `..exits.spec` is **not** imported (widgets stay schema-level).

## Design Decisions

- **`_FieldSpec` is the declarative seam**: adding a new operator
  param is a one-line addition to a `_FieldSpec` list; the row's
  `collect`/`validate` introspect the spec list, keeping
  layout + parse + format + validate in lockstep.
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
- **`_LegFrame.collect()`** returns a NEW `ExitLeg`; the dialog
  swaps the leg list wholesale on Save. No in-place mutation —
  Cancel stays safe.

## Invariants

- Widget `__init__` runs only on Tk thread.
- `collect()` raises only `ValueError` for unparseable input.
- `validate()` is read-only (no UI mutation).

## See also

- Owner: [`exits_dialog.spec.md`](exits_dialog.spec.md).
- Schema: [`../exits/model.spec.md`](../exits/model.spec.md).
- Indicator editor:
  [`scanner_block_editor.spec.md`](scanner_block_editor.spec.md).
