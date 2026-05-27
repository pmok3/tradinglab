# gui/scanner_block_editor.py — spec

> ⚠ **Tk-coupled module** — imports `tkinter`.

## Purpose

Recursive Tk widget for authoring the AND/OR block tree of a
`ScanDefinition.root`. Owns no model state — every edit mutates
the underlying `Group` / `Condition` objects in place so
`get_root()` always reflects the latest input.

## Public API

- `class BlockEditor(ttk.Frame)`:
  - `__init__(parent, *, root: Optional[Group] = None,
    default_interval: str = "5m",
    on_change: Optional[Callable[[], None]] = None)`.
  - `get_root() -> Group` — same object reference passed in.
  - `set_root(group: Group) -> None` — replace tree; rebuild widget.
  - `set_default_interval(interval: str) -> None` — used by newly
    added Conditions; existing conditions keep explicit interval.

## Internal frames

```
BlockEditor (ttk.Frame)
  └─ _root_frame: _GroupFrame
        ├─ header: enabled checkbox + AND/OR combo + add cond/group buttons + delete
        └─ children area:
              ├─ _ConditionFrame (leaf)
              │     └─ _FieldRefPicker (left) | op combo | per-op named-params row | interval combo | enabled | delete
              └─ _GroupFrame (recursive)
```

### `_FieldRefPicker`

Produces a `FieldRef`. Layout:

```
[Type ▾] [value widgets] [param widgets, may wrap] [Output ▾] [@ Symbol ▾]?
```

- Type combo: `Number` / `Builtin` / `Indicator`.
- **Number**: Entry; sets `FieldRef.literal(float(text))`. **No** Symbol
  combo — literals are symbol-independent.
- **Builtin**: Combobox over `fields.all_fields()` filtered to builtins.
  Symbol combo present (last column).
- **Indicator**: Combobox over `SCANNABLE_INDICATORS.keys()` + one
  widget per `ParamDef` (Spinbox int/float, Combobox choice,
  Checkbutton bool, Entry fallback) + Output combo over the
  allowlisted output keys (hidden when only one). Kind ids sorted
  alphabetically (`sorted(..., key=str.casefold)`); default seed
  on toggle to Indicator is the first id (`adx`). Same sort used
  by Scanner blocks, Exits/Entries indicator triggers. Symbol combo
  present (last column, after Output).
- **Symbol entry** (Builtin / Indicator only): writes `FieldRef.symbol`
  for cross-ticker references. Plain `ttk.Entry` (NOT a Combobox) —
  no dropdown, no history, no LRU, no suggestions. The user types
  ANY ticker on demand, which is the whole point of cross-symbol
  pinning. Empty entry → `ref.symbol = ""` (use active symbol).
  Typed text → uppercased on commit → `ref.symbol = "QQQ"` etc.

  **Placeholder behavior**: when the entry is empty, the displayed
  text is `(active)` in muted grey (the legacy
  `_ACTIVE_SYMBOL_SENTINEL`, aliased as `_SYMBOL_PLACEHOLDER`).
  Clicking the entry (FocusIn) clears the placeholder; tabbing out
  (FocusOut) commits the typed value AND restores the placeholder
  if empty. The `_symbol_is_placeholder` flag tracks placeholder
  vs real-value state so FocusIn doesn't wipe a real typed ticker.

  Cross-symbol pin is preserved across Builtin↔Indicator type
  toggles (`_on_type_change` carries `prev_symbol` forward). A
  small `@` glyph label sits directly before the entry to make the
  cross-symbol semantics visible at a glance in dense forms.
- **`_last_literal` cache**: Number → Field → Number preserves
  the typed numeric value.
- **Adaptive flow layout** (indicator branch): indicator combo +
  each param wrap + optional output combo + Symbol cluster tracked
  in `self._flow_children`. Reflow uses **per-row sub-frames**
  (`self._flow_row_frames`): each logical row of widgets lives in
  its own `ttk.Frame` packed `top, anchor="w"` inside the
  value_pane; widgets are packed `side="left"` inside their row
  frame. Greedy first-fit via module-level
  `_compute_flow_rows(widths, budget, pad)`. Budget =
  `max(180, (toplevel_width - 280) // X)` where `X` depends on
  the picker's `layout_hint` (see below) — `2` when sharing a row
  with a sibling picker (inline), `1` when occupying its own row
  (stacked). When the target row count changes (e.g. user picks
  a wider indicator or resizes the dialog), `_reflow_value_pane`
  tears down all flow widgets and calls
  `_build_indicator_branch_into_rows(target_row_count)` to rebuild
  fresh widgets parented to the appropriate row frames. This
  destroy-and-rebuild approach is required because Tk doesn't
  support widget reparenting, and a single shared Tk grid would
  inherit the widest column width across rows — wasting ~80 px on
  RVOL's narrow top row when the bottom row contains the long
  `Include current in denom:` label. Toplevel `<Configure>`
  triggers because no fixed-width container exists in the chain.
  Reflow debounced (`after(50, ...)`); pending callbacks cancelled
  in `_rebuild_value_pane` (avoid firing on destroyed widgets) and
  on `<Destroy>`. Param labels source from `ParamDef.description`
  (e.g. `"Include current in denom"`) with `pdef.name` as fallback
  when description is empty.
- **`layout_hint: Literal["inline", "stacked"]`** (default `"inline"`):
  optional `__init__` param + `set_layout_hint(hint)` method.
  Owned by the parent `_ConditionFrame` which propagates the
  classification to every embedded picker (LEFT + per-op field
  RHS) every time the layout flips. The hint controls only the
  flow-layout budget divisor — it does NOT change widget
  structure. Idempotent setter: re-applying the same hint is a
  no-op.

### `_ConditionFrame`

- Owns one `Condition`; **mutations in place**.
- Op combo from `OPERATOR_PARAM_SCHEMA.keys()`.
- `_NO_LEFT_OPS = {OP_INSIDE_BAR, OP_OUTSIDE_BAR, OP_NR7}`: hide
  the left `_FieldRefPicker` (structural ops ignore left operand).
- Op-change mutates existing `Condition.op` and `params` **in
  place** rather than rebinding `self.cond`. Critical: the parent
  `Group.children` list holds the same object reference; without
  in-place mutation `get_root()` would return the stale op.
- Param defaults: `FieldRef.literal(0.0)` for field slots; `1`
  for int; `1.0` for float.
- Param row rebuilds from `OPERATOR_PARAM_SCHEMA[new_op]` on op change.
- Interval combo: `"" → use default`, plus standard intervals.

#### Dual-mode layout (inline ↔ stacked)

Two grid arrangements, decided per-row by
`_classify_layout() -> "inline" | "stacked"`:

```
inline (default — 1 row):
  [✓] [LEFT picker] [op] [scalar-params] [field-params (horizontal)]
                                                        [lookback] [interval] [✕]

stacked (3 rows):
  Row 0:   [✓]              [LEFT picker  (spans 3)]            [interval] [✕]
  Row 1:   [op]   [scalar-params]   [lookback]
  Row 2:                    [field-params (vertical stack, spans 3)]
```

**Classification rule** — **fit-based**, NOT param-count based:

`_classify_layout()` returns `"stacked"` when EITHER:

1. `cond.op == OP_BETWEEN` — two RHS field pickers cannot share a
   row with the LEFT picker; always stack (semantic override).
2. `_estimate_condition_inline_width(cond) > _get_available_width()` —
   the inline rendering would overflow the dialog's available width.

Otherwise `"inline"`.

**Hysteresis** (`_HYSTERESIS_PX = 80`): when currently stacked,
flip back to inline ONLY when
`inline_estimate < available - _HYSTERESIS_PX`. Prevents
flip-flopping during a slow drag at the fit boundary.

**Fallback when toplevel not realized**: `_get_available_width()`
returns `_DEFAULT_DIALOG_WIDTH_PX = 1200` so the classifier is
deterministic during the initial build before the WM has mapped
the window. The first real `<Configure>` triggers a
reclassification against the actual width.

**`_estimate_condition_inline_width(cond)`** sums:

- chrome (enabled + op combo + lookback + interval + delete +
  paddings) ≈ 420 px,
- `_estimate_picker_width(cond.left)` (when not in `_NO_LEFT_OPS`),
- for each per-op param: scalar width OR `"name:"` label +
  `_estimate_picker_width(field_ref)`.

`_estimate_picker_width(ref)` uses calibrated font/widget metrics
(`_CHAR_PX = 7`, `_COMBO_OVERHEAD = 25`, `_SPINBOX_OVERHEAD = 20`,
`_CHECKBOX_PX = 22`, `_FRAME_PAD_PX = 6`) imported from
`gui/_widget_metrics.py` and shared with
`IndicatorDialog._compute_max_cols_for_schema` so a future
font-metric tweak propagates to both classifiers in one edit
— pure function of the ref. Reads `pdef.description or pdef.name`
for labels (matching what the renderer paints).

**Legacy helper**: `_picker_ref_is_complex(ref)` is preserved at
module scope for backward compatibility with existing test
imports — it is NOT consulted by `_classify_layout` anymore.
The old `_COMPLEX_INDICATOR_PARAM_THRESHOLD = 3` threshold is no
longer used; the param-count heuristic was replaced by direct
width measurement.

**Resize reactivity**: `_ConditionFrame.__init__` binds the
Toplevel `<Configure>` event via `_on_toplevel_resize`, which
debounces with `after(100, _do_resize_reclassify)`. On a layout
flip the handler also fires an extra `on_change` so the consumer
dialog's wheel-guard re-applies on the freshly rebuilt per-op
pickers (CLAUDE.md §7.11). Pending `after_id` and the toplevel
binding are cleaned up on `<Destroy>`.

#### Widget-identity preservation across flips

`_build()` runs **once** at construction:

```python
self._current_layout = self._classify_layout()
self._build_shared_widgets()   # enabled_chk, left_picker, op_combo,
                               # params_scalar_frame, params_fields_frame,
                               # lookback, interval_combo, delete_btn
self._build_params_row()       # destroys+creates per-op param widgets
self._apply_layout()           # re-grids shared widgets only
```

**Shared chrome widgets are never destroyed** — only re-gridded
when the layout flips. This is required by CLAUDE.md §7.11
wheel-guard contract: the consumer dialog (EntriesDialog /
ExitsDialog / ScannerTab / CustomIndicatorDialog) binds
`protect_combobox_wheel` after every `on_change` from the
editor, and rebuilt widgets need fresh bindings.

Per-op param widgets ARE destroyed + recreated by
`_build_params_row()` — this happens on op change AND on any
inline↔stacked flip that originates from a left- or param-field
change (because the field-wrap orientation inside
`_params_fields_frame` differs between layouts: horizontal in
inline, vertical in stacked).

Change handlers and their fire/re-layout responsibilities:

| Handler                  | Re-classify? | Rebuild params? | `_fire()`s        |
|--------------------------|--------------|-----------------|-------------------|
| `_on_left_change`        | yes          | only if flipped | once (twice on flip — extra for wheel guard) |
| `_on_op_change`          | yes (always rebuilds; op-change always changes the schema) | yes | once |
| `_on_param_field_change` | yes          | only if flipped | once (twice on flip) |
| `_on_toplevel_resize`    | yes (debounced 100 ms) | only if flipped | zero (one on flip — wheel-guard re-apply) |

The extra `_fire()` on flip is what lets the consumer's
wheel-guard idempotently re-apply on the brand-new picker
widgets. See `_relayout_if_needed() -> bool` — returns True
when a flip happened.

Every layout flip also calls `picker.set_layout_hint(layout)`
on the LEFT picker and on every field-kind per-op param picker,
so each picker's flow-layout budget reflects whether it shares
a row with a sibling (inline) or owns the full row (stacked).

### `_GroupFrame`

- Recursive: nested `_GroupFrame` or `_ConditionFrame` rows.
- Header: `+ Cond` / `+ Group` / `Delete` (root group's Delete hidden).
- Combinator radio: AND / OR. Mutates `Group.combinator` in place
  (lowercase normalized).
- Enabled checkbox: mutates `Group.enabled`.

## on_change semantics

Fires on every leaf edit. Consumers debounce — `ScannerTab` uses
250 ms before invoking the storage save callback.

## What we *don't* do

- Semantic validation — `engine.validate_scan` does that.
- Persistence — `ScannerTab` owns the save callback.
- Result rendering — `ScannerTab` does that.

## Quirks

- **`Condition.__post_init__` shadowing**: validation runs only
  at construction; in-place mutation of `op` + `params` does NOT
  re-validate. Op-change relies on this for atomic mutation;
  engine validation rejects nonsense on next tick.
- **`np.datetime64(tz_aware_dt)` warns about tz**: `fields.py`
  has `_to_naive_utc()` (strips tzinfo after `astimezone(UTC)`).
- **`dataclasses.field` shadowed by `OutputColumn.field`**:
  `model.py` uses `from dataclasses import field as dc_field`.

## See also

- [scanner/model](../scanner/model.spec.md),
  [scanner/fields](../scanner/fields.spec.md),
  [scanner_tab](scanner_tab.spec.md).
