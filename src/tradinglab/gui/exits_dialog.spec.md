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

class ExitsDialog(BaseModalDialog):
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
    def _on_cancel(self) -> None       # BaseModalDialog hook
    def _on_primary(self) -> None      # BaseModalDialog hook → _on_save
```

## Modal plumbing

`ExitsDialog` subclasses :class:`gui._modal_base.BaseModalDialog`
(geometry key ``"dlg.exits"``, default ``1400x780``). The base
class owns ``title`` / ``transient`` / ``grab_set`` / ESC+Return
bindings / WM_DELETE / persistent geometry; the constructor just
builds widgets + calls ``self._finalize_modal(primary=self._on_save,
cancel=self._on_cancel)`` at the end.

`_on_cancel` simply destroys the window — edits live in
``self._draft`` (a deep clone via dict round-trip) and are never
written to disk until Save, so cancel needs no explicit revert.
`_on_primary` delegates to `_on_save`. The footer ``[Close]``
button is wired to `_on_cancel` so ESC, the X, and the button
all share one path.

## Native-widget theming

The Library pane uses a classic `tk.Listbox`, so construction explicitly applies the active theme via `gui.native_theme`: `tree_bg` / `tree_fg` for rows, `spine` for selection and focus ring, and no native border. `tests/unit/gui/test_native_widget_dark_theme.py` pins the dark-mode colors.

## Combobox-wheel guard (CLAUDE.md §7.11)

``protect_combobox_wheel(self, scroll_target=self._legs_canvas)``
runs **(a)** once at the end of ``__init__`` after the initial
build, and **(b)** at the bottom of every ``_rebuild_editor``
call. The rebuild path destroys + recreates the per-leg widget
tree (trigger-kind / interval comboboxes, offset / qty spinboxes)
on every leg add/remove, OCO group mutation, or library load —
fresh widgets start with no bindings, so the guard must be
re-applied. Idempotent so the double-call at initial build is
harmless.

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
- **Mine | Templates | All filter** (audit `template-filter`). A radio
  segment above the library `Listbox` filters the saved-strategy *view*;
  it **defaults to "Mine" every time the dialog opens** (session-only
  `tk.StringVar`, NOT persisted) so the list isn't buried under the ~22
  bundled starter templates seeded on first run. A strategy is a
  *bundled template* iff its `id` starts with `tmpl-` (`_is_template`) —
  NOT `created_with.template` (a copy keeps a UUID id and stays "Mine").
  `_populate_library_listbox` rebuilds the listbox from the filtered
  subset and stores it as `self._visible_library`; `_on_library_select`
  indexes **`_visible_library`** (NOT `self._library`) so a clicked row
  maps to the correct strategy under any filter. Segment labels carry
  live counts; an empty view shows a muted hint.

## Invariants

- One ExitsDialog per `ChartApp` at a time.
- Library pane is sorted deterministically by `(name.lower(), id)`.
- Cancel produces no side effects.
- Save produces exactly one `storage.save(...)` write on success.

## See also

- Widgets: [`exits_dialog_widgets.spec.md`](exits_dialog_widgets.spec.md).
- Mirror: [`entries_dialog.spec.md`](entries_dialog.spec.md).
