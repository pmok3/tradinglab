# `gui/_modal_base.py` — Shared base classes for modal Toplevels

## Purpose

Collapses repeating modal-Toplevel boilerplate (transient + grab_set
+ geometry restore + ESC/Return bindings + footer pack order) into
two opt-in base classes.

## Public API

- `class BaseModalDialog(tk.Toplevel)`:
  - `__init__(parent, *, title="", geometry_key=None,
    default_geometry="640x480", resizable=(True, True),
    apply_dark_theme=True)`.
  - `_finalize_modal(*, primary=None, cancel=None, grab=True)` —
    call at the **end** of `__init__` after widgets exist. Wires
    `WM_DELETE_WINDOW` to `cancel`, binds `<Escape>`/`<Return>`
    via `bind_modal_keys`, restores + binds geometry via
    `geometry_store`, optionally `grab_set`s, propagates parent's
    dark theme via `parent.apply_dark_theme_to(top)` when present.
  - `_on_cancel()` — default ESC / [Cancel] (destroys).
  - `_on_primary()` — default Enter / primary handler (destroys);
    subclasses override to commit + close.

- `class BaseEditorDialog(BaseModalDialog)` — adds editor footer:
  - `_status_var: tk.StringVar` — left-aligned status slot.
  - `btn_validate`, `btn_cancel`, `btn_apply`, `btn_save_close`
    — set by `_build_editor_footer`; exposed for per-dialog disable.
  - `_build_editor_footer(parent, *, on_validate=None,
    on_cancel=None, on_apply=None, on_save_close=None,
    status_foreground=ERROR_RED) -> ttk.Frame` — builds
    `[Validate] [Apply] [Save & Close] [Cancel]` (Windows
    convention: affirmative left, Cancel rightmost). Pass `None`
    for buttons not needed. Caller packs the frame.
  - `set_status(msg, *, level="error"|"info"|"ok")` — surface
    validation message; empty msg clears. Level selects color
    (`ERROR_RED` / `MUTED_GREY` / `SUCCESS_GREEN` fallback).

- `protect_combobox_wheel(root, *, scroll_target=None) -> int` —
  walks `root`'s descendant tree and binds `<MouseWheel>` (plus
  X11 `<Button-4>` / `<Button-5>`) on every `ttk.Combobox` and
  `ttk.Spinbox` so the class binding (which on Windows / macOS
  silently advances the selected value on every wheel tick) does
  NOT fire. Returns the number of widgets guarded. Idempotent —
  re-applying after a partial widget rebuild replaces rather than
  stacks bindings. Pass `scroll_target=<canvas>` to forward the
  wheel to that canvas's `yview_scroll` first so the enclosing
  scrollable form still scrolls when the cursor sits over a
  guarded widget. Fixes the "EMA 3/8 cross became `between(0, 0)`"
  bug: accidental wheel-over-combobox in `EntriesDialog` was
  mutating the operator combobox and the corrupted strategy was
  persisted on Save. Regression test:
  `tests/unit/gui/test_combobox_wheel_guard.py`.

## Dependencies

- Internal: `._modal_keys.bind_modal_keys`,
  `.geometry_store.store`, `.colors.ERROR_RED` /
  `.colors.MUTED_GREY` / `.colors.SUCCESS_GREEN` (last with
  `ImportError` fallback).
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions

- **Two-phase init**: subclasses build widgets, then call
  `_finalize_modal` so `update_idletasks()` yields stable sizes
  before geometry restore.
- **Base class, not mixin**: boilerplate is order-sensitive
  (`grab_set` must follow `transient`).
- **Geometry key opt-in (`None` = no persistence)**: trivial
  confirm dialogs skip; complex editors pass `"dlg.entries"` etc.
- **`grab=True` default**; non-modal editors (Indicator,
  Theme Editor) override to `grab=False`.
- **Dark-theme propagation opt-in via parent hook**: silently
  no-ops if `parent.apply_dark_theme_to(top)` is absent.
- **Footer pack-from-right**: `side="right"` reverses visual
  order; packing `Cancel` first yields the canonical
  `[Validate] [Apply] [Save & Close] [Cancel]`.

## Invariants

- `_finalize_modal` idempotent — `_finalized` flag guards
  double-call.
- Geometry persistence best-effort: Tcl/OS errors during restore
  are swallowed.
- Default `_on_cancel` / `_on_primary` safe on destroyed dialog
  (errors swallowed).
- **Tk-main-thread only**.

## Usage example

```python
class MyDialog(BaseEditorDialog):
    def __init__(self, parent):
        super().__init__(parent, title="My Editor",
                         geometry_key="dlg.my_editor",
                         default_geometry="800x500")
        self._build_layout()
        footer = self._build_editor_footer(
            self,
            on_validate=self._on_validate,
            on_cancel=self._on_cancel,
            on_apply=self._on_apply,
            on_save_close=self._on_save_close,
        )
        footer.pack(fill="x", pady=(6, 0), padx=8)
        self._finalize_modal(primary=self._on_save_close)
```
