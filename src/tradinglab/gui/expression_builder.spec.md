# gui/expression_builder.py — Spec

## Purpose
The visual **"+" token-stacker** for composing an *expression operand* — a
`FieldRef(kind="expression")` (see [`scanner/model`](../scanner/model.spec.md)).
Lets the user stack atoms (fields / indicators — including *custom* ones —
/ constants) with binary operators and parentheses into an arbitrary
formula, instead of authoring a fixed left/op/right comparison. Embedded by
the `Expression` operand type in
[`_FieldRefPicker`](scanner_block_editor.spec.md), so it lights up in
Entries / Exits / Scanner condition builders (the downstream consumers where
custom indicators are invoked).

## Public API
- `class ExpressionBuilder(ttk.Frame)` — `__init__(master, *, ref=None, on_change=None, data_status_provider=None)`.
  - `get() -> FieldRef` — the current `FieldRef(kind="expression")`.
  - `set(ref)` — replace the token list from an expression ref (or clear).
  - `is_valid() -> bool` — `validate_expression(terms)[0]`.
- `operand_summary(ref) -> str` — short chip label (`0.5`, `close`, `ema(9)`, `( … )`).
- `expression_text(terms) -> str` — one-line preview (`"ema(9) * 0.5 + rsi(14)"`).

## Layout
A horizontal chip strip (one chip per token) + a trailing `+` menu + a
live preview / validity line:
- **Operand chip** — the operand summary + an edit (`✎`, opens
  `_OperandDialog`) + remove (`✕`).
- **Operator chip** — a readonly `ttk.Combobox` over `EXPR_OPS`
  (`+ - * / % ** ( )`) + remove.
- **`+` menu** (`ttk.Menubutton`) — `Value…` (appends an operand via
  `_OperandDialog`) or an `Operator` cascade.
- **Preview line** — `= <expression text>` (muted) + `✓ valid` (green) or
  `✕ <reason>` (`ERROR_RED`) from `validate_expression`.

`_OperandDialog(BaseModalDialog)` wraps a `_FieldRefPicker` (lazy-imported to
break the `scanner_block_editor ↔ expression_builder` cycle) to choose /
edit one operand — so the full categorized field / indicator surface
(incl. custom indicators) is reused. An expression-kind operand is coerced
to `close` there (nesting is reached via parentheses, not picker recursion).

## Dependencies
- Internal: [`scanner.model`](../scanner/model.spec.md) (`ExprToken`, `FieldRef`, `validate_expression`, `EXPR_*`), `gui._modal_base` (`BaseModalDialog`, `protect_combobox_wheel`), `gui.colors` (`ERROR_RED`, `MUTED_GREY`, `up_green`), `gui.menu_theme`, `gui.native_theme`. `_FieldRefPicker` is imported **lazily** inside `_OperandDialog`.
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions
- **Reuses the model + engine as-is** — the builder only composes an
  infix `ExprToken` list; evaluation is the pure `evaluate_expression`
  (engine resolves each leaf), so custom indicators are first-class atoms
  with no engine changes.
- **Operand editing reuses `_FieldRefPicker`** — no bespoke value picker;
  every operand gets the categorized field / indicator / constant surface.
- **Lazy `_FieldRefPicker` import** breaks the mutual import cycle with
  `scanner_block_editor` (which imports `ExpressionBuilder` at top).
- **Semantic colors via `gui.colors`** (`up_green()` / `ERROR_RED` /
  `MUTED_GREY`) — imported names, not literals, so the theme-invariant gate
  is satisfied.
- **`on_change` fires on every mutation** so the embedding picker /
  condition propagates edits (and the consumer re-applies its wheel-guard).

## Invariants
- `get().kind == "expression"` always; `get().terms` mirrors the chip strip
  in order.
- A malformed / empty token list is representable (the builder shows
  `✕ <reason>`); the engine returns `None` for it, so a broken expression
  never fires a condition.

## Testing
- `tests/scanner/test_expression_builder_gui.py` — helpers, empty/seeded
  builder round-trip, mutation methods (`_add_op` / `_set_op` / `_remove`)
  fire `on_change`, picker `Expression` type presence + switch-in/out,
  and BlockEditor round-trip of an expression operand.
- Model / engine coverage: `tests/scanner/test_expression_model.py`,
  `tests/scanner/test_expression_engine.py`.
