# `gui/_modal_keys.py` — Shared ESC / Return key bindings for modal dialogs

## Purpose
The UI/UX audit (May 2026) found that `<Escape>` and `<Return>`
behaviour was inconsistent across the 12+ modal Toplevels in the
GUI — some dialogs bound only one, some neither, and a few bound
Return inside multi-line Text widgets, which prevented the user
from inserting newlines in journal fields. This helper exists so
each dialog can opt in with a single call and get the same
semantics everywhere.

## Public API
- `bind_modal_keys(toplevel, *, cancel=None, primary=None)` — wire
  `<Escape>` → `cancel` and `<Return>` / `<KP_Enter>` → `primary`
  on `toplevel`. Either callback may be `None` to suppress that
  binding (e.g. read-only dialogs pass `primary=None`; confirm
  dialogs that must not be ESC-dismissable pass `cancel=None`).

## Behaviour
- `<Escape>` always invokes `cancel` and returns `"break"` so the
  binding doesn't propagate to ancestor widgets.
- `<Return>` is **suppressed when a multi-line `tk.Text` widget
  has focus** (see `_focus_is_multiline_text`). This is the
  load-bearing detail: `tk.Text` widgets need Enter to insert
  newlines (PreTrade thesis, PostTrade review, notes fields), so
  the helper checks the focus widget's class first and fires
  `primary` for any non-`tk.Text` focus (including single-line
  `ttk.Entry` / `ttk.Combobox` / button).
- Numeric keypad Enter (`<KP_Enter>`) is bound to the same
  handler as `<Return>` so accountants happy on the 10-key still
  submit cleanly.

## Dependencies
- External: `tkinter` (stdlib).
- Internal: none.

## Design Decisions
- **Helper function, not a mixin**: every Toplevel calls
  `bind_modal_keys(self, ...)` exactly once in `__init__`. A
  mixin would force inheritance ordering and provide no benefit.
- **Focus-class check, not a per-widget opt-out**: requires zero
  per-dialog plumbing — the dialog can drop a `tk.Text` anywhere
  and Enter Just Works. Adding a multi-line Text to an existing
  dialog never breaks the dialog's submit handler retroactively.
- **`"break"` return** from the handlers prevents bubbling to
  outer widget bindings (the chart canvas binds Return to
  "reset view" — without `break`, dismissing a dialog would also
  reset the chart underneath it).
- **Callback exceptions surface via Tk's
  `report_callback_exception`** rather than crashing the dialog.
  A buggy `cancel` handler shouldn't leave the user trapped in a
  modal.

## Invariants

- After `bind_modal_keys(top, cancel=c)`: `top` responds to
  `<Escape>` by calling `c()` and returning `"break"`.
- After `bind_modal_keys(top, primary=p)`: `top` responds to
  `<Return>` / `<KP_Enter>` by calling `p()` unless a
  `tk.Text`-class widget has focus, in which case the event
  passes through to insert a newline.
- Safe to call multiple times on the same Toplevel (later calls
  replace earlier bindings — Tk's `bind` is single-handler by default).
