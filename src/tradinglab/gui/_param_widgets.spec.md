# `_param_widgets.py` — Shared ParamDef widget construction

## Purpose

Single source of truth for the bool / choice / int / float / str
widget dispatcher that previously lived in 3 sites:

1. `gui/indicator_dialog.py::_build_one_param_widget` (~150 LOC,
   debounced commits with eager checkbox/combobox/spinbox-arrow).
2. `gui/scanner_block_editor.py::_FieldRefPicker._build_param_widget`
   (~52 LOC, eager commits).
3. `gui/scanner_block_editor.py::_ConditionFrame._build_params_row`
   (inline scalar branch — raw Vars, manual commits).

All three handled `pdef.kind == "bool" / "choice" / "int" / "float" /
"str"` (plus the `choices` override and the `anchor_ts` special-case
for Anchored VWAP). Each was patched independently for the §7.19
`pdef.description`-label fix; consolidating into one helper means
future kinds and label conventions land once.

## Public surface

```python
build_param_widget(
    parent, pdef, seed, *,
    on_change=None,
    commit_policy="eager",
    debounce_ms=250,
    choices_override=None,
    width=None,
    anchor_pick_callback=None,
) -> tuple[tk.Variable, tk.Widget]
```

Returns `(var, widget)`. The caller is responsible for placing the
widget via `grid()` / `pack()` AND for rendering the label —
:func:`label_text_for` produces the canonical text (description or
name + `":"`).

### `CommitPolicy` values

| Policy          | When `on_change` fires                                                         | Use case                                                              |
|-----------------|--------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| `"eager"`       | Every `trace_add('write')` event (typing, arrow, combobox pick, checkbox flip) | `scanner_block_editor._FieldRefPicker._build_param_widget`            |
| `"debounced"`   | Coalesced — fires `debounce_ms` after the last var write                       | `indicator_dialog._build_one_param_widget` (250 ms typing debounce)   |
| `"on_focus_out"`| Only on `<FocusOut>` or `<Return>`                                             | Free-text fields where every keystroke shouldn't fire downstream work |
| `"manual"`      | Never — caller consumes `var.get()` itself                                     | `_ConditionFrame._build_params_row` scalar branch                     |

### `choices_override`

Lets the caller swap out `pdef.choices` without mutating the
ParamDef instance — useful when an indicator name list is filtered
by scope or scanner context. When `choices_override` is `None`,
`pdef.choices` is used.

### `width`

Per-kind defaults: choice 10, int/float 6, str 14. Callers with
schema-driven widths (`indicator_dialog` uses
`_combo_width_for_choices` / `_spinbox_width_for`) pass `width=`
explicitly. Bool ignores width.

### `anchor_pick_callback`

When `pdef.kind == "str"` AND `pdef.name == "anchor_ts"`, the
helper builds a read-only label + "Pick Anchor…" Button pair (the
Anchored VWAP convention). The button command is wired to
`anchor_pick_callback` (defaults to no-op). The returned `widget`
is a `ttk.Frame` containing the label+button cluster.

## Why this is separate from `exits_dialog_widgets._render_field`

The exits dialog's `_render_field` consumes a DIFFERENT kind
taxonomy: `"float"` / `"int"` / `"time_str"` / `"enum"` /
`"enum_with_none"` / `"enum_str"`. Those kinds aren't `ParamDef`
kinds — they're a parallel widget taxonomy used by `ExitFieldSpec`.
Audit #8 covers consolidating that helper into a sibling module; do
NOT cross-pollinate the two helpers without that audit.

## §7.19 label contract

Every consumer of `build_param_widget` MUST use
:func:`label_text_for` (or read `pdef.description` first, falling
back to `pdef.name`) for the visible label. This keeps wide
descriptions (`"Include current in denom"`) consistent across
Scanner / Entries / Exits / Indicator dialogs. The same convention
is used by the layout-classifier's pixel estimator
(`scanner_block_editor._estimate_picker_width`) so width
calculations stay aligned with on-screen rendering.

## Tests

`tests/unit/gui/test_build_param_widget.py` pins:

- Every `ParamDef.kind` returns the right `tk.Variable` subclass +
  widget type.
- `choices_override` swaps the choice list.
- Each `commit_policy` fires (or doesn't fire) per the table above.
- `debounced` coalesces N rapid writes into one call.
- `anchor_ts` special case returns the Frame + label-formatting trace.
- `pdef.description` overrides `pdef.name` in `label_text_for`.

## Wheel-guard interaction (§7.11)

The Combobox / Spinbox widgets created here are subject to the
wheel-mutation footgun. Callers MUST re-run
`protect_combobox_wheel(root, scroll_target=canvas)` on the parent
Toplevel after `build_param_widget` returns, OR rely on the
ambient `BaseModalDialog.__init__` guard (audit #4). The helper
itself does NOT call `protect_combobox_wheel` — it has no handle
on the parent canvas or scroll target.
