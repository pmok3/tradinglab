# `gui/_modal_base.py` — Shared base classes for modal Toplevels

## Purpose

Collapses repeating modal-Toplevel boilerplate (transient + grab_set
+ geometry restore + ESC/Return bindings + footer pack order) into
two opt-in base classes, plus shared combobox-wheel and scrollable-form
helpers.

## Public API

- `class BaseModalDialog(tk.Toplevel)`:
  - `__init__(parent, *, title="", geometry_key=None,
    default_geometry="640x480", resizable=(True, True),
    apply_dark_theme=True)`.
  - `_finalize_modal(*, primary=None, cancel=None, grab=True)` —
    call at the **end** of `__init__` after widgets exist. Wires
    `WM_DELETE_WINDOW` to `cancel`, binds `<Escape>`/`<Return>`
    via `bind_modal_keys`, restores + binds geometry via
    `    geometry_store`, optionally `grab_set`s, paints the Toplevel
    `bg` with the active theme's `win_bg` via
    `native_theme.apply_toplevel_theme`, then propagates the
    parent or parent-master dark theme via
    `apply_dark_theme_to(top)` when present.
  - `_on_cancel()` — default ESC / [Cancel] (destroys).
  - `_on_primary()` — default Enter / primary handler (destroys);
    subclasses override to commit + close.

- `class BaseEditorDialog(BaseModalDialog)` — adds editor footer.
  Adopted by `gui.entries_dialog.EntriesDialog` and
  `gui.exits_dialog.ExitsDialog` (audit item #4; the class previously
  had **zero** subclasses — written but never adopted). Members:
  - `_status_var: tk.StringVar` — left-aligned status slot.
  - `btn_validate`, `btn_cancel`, `btn_apply`, `btn_save_close`
    — set by `_build_editor_footer`; exposed for per-dialog disable.
  - `_build_editor_footer(parent, *, on_validate=None,
    on_cancel=None, on_apply=None, on_save_close=None,
    status_foreground=ERROR_RED, validate_text="Validate",
    apply_text="Apply", save_close_text="Save & Close",
    cancel_text="Cancel") -> ttk.Frame` — builds
    `[Validate] [Apply] [Save & Close] [Cancel]` (Windows
    convention: affirmative left, Cancel rightmost). Pass `None`
    for buttons not needed. The four `*_text` overrides let an
    adopting dialog with different wording share the builder without
    changing its user-visible labels (e.g. `ExitsDialog` passes
    `save_close_text="Save"`, `cancel_text="Close"`, and omits
    `on_apply` for `[Validate] [Save] [Close]`); the pack **order** is
    unchanged. Caller packs the frame. Order is pinned by
    `tests/unit/gui/test_dialog_button_order_windows.py::test_modal_base_editor_footer_order_windows`
    (which now scans the `self.btn_*` assignment/pack sequence, since the
    labels are parameters now) and by the unchanged live-widget geometry
    check in `tests/unit/gui/test_modal_base.py` (the strongest ordering
    guard).
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
  The codebase-wide invariant is pinned by
  `tests/unit/gui/test_modal_invariants.py`: every concrete
  `BaseModalDialog` / `BaseEditorDialog` subclass must call
  `protect_combobox_wheel(...)` unless it is explicitly exempted
  because it has no `ttk.Combobox` / `ttk.Spinbox`.

- `make_scrollable_form(parent, *, horizontal=False,
  bind_mousewheel=True) -> tuple[ttk.Frame, tk.Canvas]` — builds a
  `Canvas` + scrollbar(s) + inner `ttk.Frame` form skeleton. The
  returned canvas is the intended `scroll_target` for
  `protect_combobox_wheel`. The classic `tk.Canvas` is painted with
  the active theme's `win_bg` via `native_theme.apply_canvas_theme(
  canvas, current_theme(parent))` at creation — the ttk
  `ThemeController` sweep does not reach a `tk.Canvas`, so the scroll
  gutter would otherwise show bright white in dark mode (CLAUDE.md
  §7.31). This centrally dark-themes every dialog's scrollable form;
  pinned by `tests/unit/gui/test_native_widget_dark_theme.py`
  (ExitsDialog canvas case + the per-window meta-test). When
  `bind_mousewheel=True`, canvas enter/leave installs and removes
  global wheel bindings, with an inner-frame destroy backstop so the
  binding does not leak after dialog close.
  - **No-scroll-when-fitting guard.** The wheel handlers consult an
    internal `_v_can_scroll()` predicate that returns `True` only when
    the form content overflows the viewport (`canvas.yview()` is not
    `(0.0, 1.0)`). When the content fully fits — e.g. a single-parameter
    indicator form like LRSI's lone `gamma` — wheel / `<Button-4>` /
    `<Button-5>` events become no-ops (still returning `"break"`). This
    suppresses Tk's canvas quirk where `yview_scroll` shifts the view
    even when the scrollregion is smaller than the canvas, which
    previously let users drag a lone widget around. Because every
    indicator param popup and the four other dialog callers share this
    helper, the fix is the single templated contract for all of them.
    The handler and predicate are exposed as `canvas._tl_wheel_handler`
    and `canvas._tl_v_can_scroll` for headless tests
    (`tests/unit/gui/test_field_ref_param_dialog.py`).

## Dependencies

- Internal: `._modal_keys.bind_modal_keys`,
  `.geometry_store.store`, `.colors.ERROR_RED` /
  `.colors.MUTED_GREY` / `.colors.SUCCESS_GREEN` (last with
  `ImportError` fallback), `.native_theme.apply_canvas_theme`,
  `.native_theme.apply_toplevel_theme`, `.native_theme.current_theme`.
- External: `tkinter`, `tkinter.ttk`, `typing.Any`.

## Design Decisions

- **Two-phase init**: subclasses build widgets, then call
  `_finalize_modal` so `update_idletasks()` yields stable sizes
  before geometry restore.
- **Base class, not mixin**: boilerplate is order-sensitive
  (`grab_set` must follow `transient`).
- **Geometry key opt-in (`None` = no persistence)**: trivial
  confirm dialogs skip; complex editors pass `"dlg.entries"` etc.
- **`grab=True` default**; non-modal viewers / editors (Doc Viewer,
  Drawing, Indicator) override to `grab=False`.
- **Dark-theme propagation is a base-class guarantee, not a per-dialog
  chore**: when `apply_dark_theme=True` (default), `_finalize_modal`
  paints the Toplevel's own classic `bg` with the active theme's
  `win_bg` via `native_theme.apply_toplevel_theme(self,
  current_theme(self))`. `ttk.Style` reaches ttk children but never the
  Toplevel background, so every region the dialog's content does not
  cover — the padding gutters, and the right/bottom slack on any
  resizable dialog whose form is smaller than the restored geometry —
  rendered in the bright system default under dark mode. Doing it here
  rather than per dialog is deliberate: the identical bug shipped twice
  (Prepare Universe, then Start Sandbox Session) because the fix was
  hand-rolled at the call site each time (`CLAUDE.md` §7.34). Covering
  the full span still needs the dialog to grant its content grid
  weights, but the painted background means forgetting that degrades to
  a cosmetic gap rather than a half-lit window.
  The legacy `apply_dark_theme_to(top)` parent hook is still consulted
  afterwards (parent, then its `master`) for apps that want richer
  per-dialog tinting, and silently no-ops when absent.
- **Footer pack-from-right**: `side="right"` reverses visual
  order; packing `Cancel` first yields the canonical
  `[Validate] [Apply] [Save & Close] [Cancel]`.

## Invariants

- `_finalize_modal` idempotent — `_finalized` flag guards
  double-call.
- After `_finalize_modal` with `apply_dark_theme=True`, the Toplevel's
  classic `bg` equals the active theme's `win_bg` — pinned by
  `tests/unit/gui/test_native_widget_dark_theme.py::test_base_modal_dialog_paints_toplevel_dark`.
  Theme resolution and the paint are both best-effort: a parent with no
  `_theme_ctrl` resolves to `LIGHT_THEME`, and a `TclError` is swallowed.
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
