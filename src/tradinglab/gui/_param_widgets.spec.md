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
    on_commit_eager=None,
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

`combo_width_for_choices(choices) -> int` returns the schema-driven
Combobox width used by every ParamDef consumer: longest choice length
+ 2 chars, floor 8, cap 30, and legacy fallback 10 for empty choices.

`spinbox_width_for(pdef) -> int` returns a Spinbox width based on the
longer of `pdef.min` / `pdef.max`, plus a small sign/decimal buffer,
clamped to `[6, 14]`.

`tooltip_text_for(pdef) -> str` returns short, non-essential tooltip
text for ParamDef-driven controls. It supplies specific explanations
for known advanced params (`denominator_includes_current`,
`session_filter`, `z_score`, `compare_symbol`) and otherwise derives a
generic hint from label, choices, and numeric range.

`validate_param_value(pdef, raw) -> (ok, value, message)` validates
and coerces one raw Tk variable value. It returns the coerced value and
empty message on success; on failure it returns `pdef.default` plus a
short inline message such as `Enter Length greater than or equal to 1.`
Callers decide whether to block save/commit. Integer params accept only
finite whole-number text (`"2"` / `"2.0"`), never silently truncating a
fractional decimal such as `"1.5"`. Float params reject non-finite input
(`nan`, `inf`, `-inf`) before range checks.

`param_group_for(pdef) -> "Basic" | "Advanced"` returns the simple UI
group used by builder controls. Known advanced fields such as
`session_filter`, `denominator_includes_current`, `z_score`,
`compare_symbol`, and `anchor_ts` are Advanced; all other fields
default to Basic.

### `CommitPolicy` values

| Policy          | When `on_change` fires                                                         | Use case                                                              |
|-----------------|--------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| `"eager"`       | Every `trace_add('write')` event (typing, arrow, combobox pick, checkbox flip) | `scanner_block_editor._FieldRefPicker._build_param_widget`            |
| `"debounced"`   | Coalesced after `debounce_ms`; discrete Checkbutton / Combobox / Spinbox commits can fire `on_commit_eager` immediately | `indicator_dialog._build_one_param_widget` (250 ms typing debounce)   |
| `"on_focus_out"`| Only on `<FocusOut>` or `<Return>`                                             | Free-text fields where every keystroke shouldn't fire downstream work |
| `"manual"`      | Never — caller consumes `var.get()` itself                                     | `_ConditionFrame._build_params_row` scalar branch                     |

### `choices_override`

Lets the caller swap out `pdef.choices` without mutating the
ParamDef instance — useful when an indicator name list is filtered
by scope or scanner context. When `choices_override` is `None`,
`pdef.choices` is used.

### `width`

Per-kind defaults: choice 10, int/float 6, str 14. Callers with
schema-driven widths (`indicator_dialog` and `_FieldRefPicker` use
`combo_width_for_choices` / `spinbox_width_for`) pass `width=`
explicitly. Bool ignores width.

### `anchor_pick_callback`

When `pdef.kind == "str"` AND `pdef.name == "anchor_ts"`, the
helper builds a read-only label + "Pick Anchor…" Button pair (the
Anchored VWAP convention). The button command is wired to
`anchor_pick_callback` (defaults to no-op). The returned `widget`
is a `ttk.Frame` containing the label+button cluster. The label
formats the anchor via `_format_anchor_label` — a blank/unset anchor
reads **"Not set"** (AVWAP anchors are symbol-keyed + explicit; there
is no auto-first-eligible default). This `_format_anchor_label` MUST
stay byte-identical to `indicator_dialog._format_anchor_label`.

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
- `tooltip_text_for` explains advanced RVOL/RRVOL params such as
  `"Include current in denom"`.
- `validate_param_value` rejects out-of-range numeric values and
  unknown choices with actionable messages, including fractional text
  for integer params and non-finite float values.
- Schema-driven Combobox / Spinbox width helpers fit long choices such
  as `"regular_plus_premarket"`.

## Wheel-guard interaction (§7.11)

The Combobox / Spinbox widgets created here are subject to the
wheel-mutation footgun. Callers MUST re-run
`protect_combobox_wheel(root, scroll_target=canvas)` on the parent
Toplevel after `build_param_widget` returns, OR rely on the
ambient `BaseModalDialog.__init__` guard (audit #4). The helper
itself does NOT call `protect_combobox_wheel` — it has no handle
on the parent canvas or scroll target.
