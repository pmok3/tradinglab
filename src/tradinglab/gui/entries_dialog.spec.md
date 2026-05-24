# gui/entries_dialog.py — Spec

## Purpose

Modal editor for one `EntryStrategy`. Tabbed form so the strategy
schema's six concerns (identity, universe, trigger, sizing, on-fill
exits, lifecycle) each get their own surface area. Drafts are
half-validated continuously; final validation runs on Save via
`validate_strategy`.

## Tabs

| Tab            | Fields                                                                  |
| -------------- | ----------------------------------------------------------------------- |
| **Identity**   | Name, Direction (LONG/SHORT), Label                                     |
| **Universe**   | Mode (symbols / scanner / from-attached-chart); symbol list / scanner-id |
| **Trigger**    | Kind (six radio buttons) → kind-specific subform; INDICATOR embeds `BlockEditor` |
| **Sizing**     | Sizing kind (FIXED_QTY / FIXED_NOTIONAL), qty/notional, ShareRounding   |
| **On-fill exits** | Multi-select list of exit-strategy ids (looked up via `ExitEvaluator.all_strategies`) |
| **Lifecycle**  | Cooldown, max-fires/symbol, max-fires/total, position-already-open policy, arm-window start/end, require-market-open |

## Public API

```python
class EntriesDialog(tk.Toplevel):
    def __init__(self, master, *, app, strategy: Optional[EntryStrategy],
                 on_save: Optional[Callable[[EntryStrategy], None]] = None) -> None
    def _build_tab_identity(self) -> ttk.Frame
    def _build_tab_universe(self) -> ttk.Frame
    def _build_tab_trigger(self) -> ttk.Frame
    def _build_tab_sizing(self) -> ttk.Frame
    def _build_tab_on_fill_exits(self) -> ttk.Frame
    def _build_tab_lifecycle(self) -> ttk.Frame
    def _collect(self) -> EntryStrategy   # raises ValueError on missing fields
    def _on_save(self) -> None
    def _on_cancel(self) -> None
```

## Dependencies

- `..entries.{model, storage}` for schema + persistence.
- `.scanner_block_editor.BlockEditor` (re-used) for INDICATOR triggers.
- `..exits.storage.load_all` to populate the on-fill exits picker.
- `..scanner.storage.load_all` for the SCANNER_ALERT scanner picker.

## Design Decisions

- **Tab-per-concern, not one tall form.** Six tabs keep each surface
  small. Save is enabled only when minimal required fields are
  filled (cheap pre-check; full validate runs on click).
- **INDICATOR trigger embeds `BlockEditor`.** Re-uses the
  scanner condition-tree editor to avoid duplicating the
  operator/threshold UX. The dialog binds the editor's
  `Group` back into `EntryTrigger.condition` on save.
- **`threshold_warn` / `threshold_extreme` fields are NOT shown**
  on the RVOL / RRVOL operator subforms for indicator triggers —
  removed in the recent five-item UX batch. They were always purely
  cosmetic chart-overlay reference lines and never affected trigger
  evaluation. The fields still exist on the underlying operator
  classes for backward-compatible JSON loads.
- **SCANNER_ALERT picker** dropdown shows scanner names from
  `scanner.storage.load_all`; the displayed scanner id is the value
  stored in `EntryTrigger.scanner_id`.
- **Save calls `validate_strategy` and surfaces all errors** in a
  message box; doesn't dismiss the dialog on validation failure.
- **`on_save` callback** lets the parent `EntriesTab` refresh its
  Treeview without the dialog reaching back into app state.

## Invariants

- **Cancel produces no side effects** (in-memory draft is discarded).
- **Combobox / Spinbox wheel-guard installed dialog-wide.** After
  `_build_layout` and after every dynamic widget rebuild
  (`_render_trigger_params`, `_render_universe_params`,
  `BlockEditor` op changes via `_on_block_editor_changed`), the
  dialog calls `_protect_combobox_wheel()` which delegates to
  `_modal_base.protect_combobox_wheel(self, scroll_target=self._form_canvas)`.
  Without this, mouse-wheel scrolling over the form (the dialog
  `bind_all`s `<MouseWheel>` for canvas scroll) would silently
  advance the operator / interval / sizing combobox values on
  every wheel tick — the documented "EMA 3/8 cross became
  `between(0, 0)` after saving" corruption was caused by exactly
  this. Regression test:
  `tests/unit/gui/test_combobox_wheel_guard.py`.
- Save produces exactly one `storage.save(...)` write on success.
- The dialog never directly mutates the live `EntryEvaluator`
  library — only via `storage.save` → tab refresh → evaluator's
  next `set_strategies` rebuild.

## See also

- Mirror: [`exits_dialog.spec.md`](exits_dialog.spec.md).
- Schema: [`../entries/model.spec.md`](../entries/model.spec.md).
- Embedded editor: [`scanner_block_editor.spec.md`](scanner_block_editor.spec.md).
