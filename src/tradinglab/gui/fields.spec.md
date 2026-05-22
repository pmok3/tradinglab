# `gui/fields.py` — Shared labeled-field widgets for modal dialogs

## Purpose

Composable toolkit to build `[label] [content] [error]` form rows
with consistent spacing — replaces ~12 hand-rolled per-dialog
`Label + Entry/Combobox + error-label` patterns.

## Public API

- `class FieldRow(parent, label, *, label_width=18, error_var=None,
  **frame_kwargs)` — bare row whose middle slot is a `ttk.Frame`
  (`row.content`) the caller fills.
  - `row.content` — middle frame.
  - `row.label` — `ttk.Label` (mutable text).
  - `row.error_var` — `tk.StringVar` mirrored by right-side error
    label.
  - `row.set_error(msg)` / `row.clear_error()`.
  - Internal: 3-column grid; col 1 (content) expands.
- `LabeledEntry(parent, label, *, textvariable=None, show=None,
  width=None, label_width=18, error_var=None, state=None,
  **entry_kwargs) -> (FieldRow, ttk.Entry)`.
- `LabeledCombobox(parent, label, *, textvariable=None, values=(),
  width=None, label_width=18, state="readonly", error_var=None,
  **combo_kwargs) -> (FieldRow, ttk.Combobox)`. `state="normal"`
  for editable.
- `LabeledCheckbutton(parent, label, *, variable=None, text=None,
  label_width=18, error_var=None, **chk_kwargs) -> (FieldRow,
  ttk.Checkbutton)`. `text` = checkbutton caption; `label` = row's
  left-side label.
- `LabeledSpinbox(parent, label, *, textvariable=None, from_=0,
  to=100, increment=1, width=None, label_width=18, error_var=None,
  **spin_kwargs) -> (FieldRow, ttk.Spinbox)`.

Factories return `(row, widget)` so callers can grab the
variable / focus without re-querying the content frame.

## Layout

3-column `grid`:

| Col 0          | Col 1                  | Col 2          |
|----------------|------------------------|----------------|
| `[Label:]`     | `<content widget(s)>`  | `<error text>` |
| width=18, anchor=e | sticky=ew, expand=1 | foreground=ERROR_RED |

Helpers never call `.pack()` on the row — caller decides placement.
Trailing `:` is auto-added (de-duped if caller already added).

## Constants

- `_DEFAULT_LABEL_WIDTH = 18` — chosen to cover current labels
  without wrapping.
- `_ROW_PADY = (2, 2)`, `_LABEL_PADX = (0, 8)`, `_ERROR_PADX = (8, 0)`.

## Dependencies

- Internal: `.colors.ERROR_RED`.
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions

- **`content` is a frame**, not the widget itself, so compound rows
  (Entry + eyeball, Combobox + edit button) can grid multiple
  widgets.
- **Factories return `(row, widget)`** — callers need the widget's
  `textvariable` / focus / per-widget bindings.
- **Row owns the error var** — no per-dialog
  `_field_errors: Dict[str, StringVar]` bookkeeping.
- **No internal `.pack()`** — composable with both top-level
  dialogs and notebook page grids.
- **Trailing-colon normalisation**: `"Name"` or `"Name:"` both
  render as `"Name:"`.

## Invariants

- `row.error_var.get() == ""` means error slot hidden.
- 3-column grid; col 1 (content) is the only expandable one.
- `LabeledCombobox` defaults `state="readonly"`; editable opts in
  with `state="normal"`.
- **Tk-main-thread only**.
