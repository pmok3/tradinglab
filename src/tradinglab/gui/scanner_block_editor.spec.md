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
- **Symbol combo** (Builtin / Indicator only): writes `FieldRef.symbol`
  for cross-ticker references. Default value is the `(active)`
  sentinel (literal string); committing it yields `ref.symbol = ""`.
  Combo is `state="normal"` (freely typeable). Its values list is
  `("(active)",) + tuple(recent_cross_symbols_lru)` so a user's
  recent picks are one click away. A picked / typed ticker is
  uppercased + pushed to the front of `_recent_cross_symbols` (a
  module-level LRU mirroring the persisted
  `_settings.set("recent_cross_symbols", [...])` value, capped at
  `_RECENT_CROSS_SYMBOLS_CAP = 20`). Persistence is best-effort —
  failures degrade silently to in-memory-only. Cross-symbol pin is
  preserved across Builtin↔Indicator type toggles (`_on_type_change`
  carries `prev_symbol` forward). A small `@` glyph label sits
  directly before the combo to make the cross-symbol semantics
  visible at a glance in dense forms.
- **`_last_literal` cache**: Number → Field → Number preserves
  the typed numeric value.
- **Adaptive flow layout** (indicator branch): indicator combo +
  each param wrap + optional output combo + optional Symbol cluster
  tracked in `self._flow_children`, re-gridded by
  `_reflow_value_pane()` on Toplevel resize. Greedy first-fit via
  module-level `_compute_flow_rows(widths, budget, pad)`. Budget =
  `max(180, (toplevel_width - 280) // 2)` (~280px chrome reservation,
  halved for sibling picker). Toplevel `<Configure>` triggers
  because no fixed-width container exists in the chain. Reflow
  debounced (`after(50, ...)`); pending callbacks cancelled in
  `_rebuild_value_pane` (avoid firing on destroyed widgets) and
  on `<Destroy>`.

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
