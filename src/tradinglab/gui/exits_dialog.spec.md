# gui/exits_dialog.py — Spec

## Purpose

Top-level modeless editor for one `ExitStrategy`. Two panes:
**Library** (saved strategies / templates) and **Editor** (leg +
trigger form). Edits are committed via Save (atomic JSON write +
evaluator notify). The dialog is a singleton per-app: re-opening
focuses the existing window rather than spawning a duplicate.

## Public API

```python
def open_exits_dialog(app: "ChartApp", *, strategy_id: Optional[str] = None,
                      attach_to_position: Optional[str] = None) -> None
    """Singleton entry-point: open the dialog (focus if already open),
    optionally pre-loading a strategy and queuing an attach-on-save."""

def make_bracket_strategy(*, name: str, target_pct: float,
                          stop_pct: float, qty: float) -> ExitStrategy
    """Helper used by the 'New Bracket' template button."""

class ExitsDialog(tk.Toplevel):
    def __init__(self, master, *, app, strategy: Optional[ExitStrategy],
                 attach_to_position: Optional[str] = None) -> None
    def refresh_library(self) -> None
    def load_strategy(self, strategy_id: str) -> None
    def _on_new_blank(self) -> None
    def _on_new_bracket(self) -> None
    def _on_duplicate(self) -> None
    def _on_delete(self) -> None
    def _on_save(self) -> None
    def _on_save_as_template(self) -> None
    def _on_cancel(self) -> None
```

## Layout

```
┌─ Library ──────┬─ Editor ─────────────────────────────────────────┐
│  Name | Kind   │  Identity (name / direction / position-binding) │
│  ─────────     │  Legs   (one _LegFrame per leg, expandable)     │
│  [New]         │    [_TriggerRow]+ inside _LegFrame              │
│  [Bracket]     │    [_OCOGroupRow] for sibling-leg OCO grouping  │
│  [Duplicate]   │  Lifecycle (cooldown / arm-window / etc.)       │
│  [Delete]      │                                                  │
│  [Import]      │                                                  │
│  [Export]      │  [Save] [Save as Template] [Cancel]              │
└────────────────┴──────────────────────────────────────────────────┘
```

## Dependencies

- `..exits.{model, storage}`.
- `.exits_dialog_widgets._BracketDialog / _LegFrame / _TriggerRow /
  _OCOGroupRow / _FieldSpec`.
- `..exits.evaluator.ExitEvaluator` (for live-update notification).

## Design Decisions

- **Modeless** — leaves the chart usable while editing. Closing the
  chart's main window prompts to discard unsaved changes.
- **Singleton via `_open_dialog` ref on `ChartApp`.** Prevents two
  concurrent editors writing the same file.
- **Bracket helper** (`make_bracket_strategy`) is a function so unit
  tests can build a bracket without instantiating Tk.
- **`attach_to_position`** is a *queued* hint: on successful save the
  dialog calls `_exit_evaluator.attach_strategy(pos_id, strategy)`
  — convenience for the "Attach → New strategy" UX in the exits tab.
- **Save runs `validate_strategy`** and surfaces all errors in a
  message box; doesn't dismiss on failure.

## Invariants

- One ExitsDialog per `ChartApp` at a time.
- Library pane is sorted deterministically by `(name.lower(), id)`.
- Cancel produces no side effects.
- Save produces exactly one `storage.save(...)` write on success.

## See also

- Widgets: [`exits_dialog_widgets.spec.md`](exits_dialog_widgets.spec.md).
- Mirror: [`entries_dialog.spec.md`](entries_dialog.spec.md).
