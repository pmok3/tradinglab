# Contributing

## Spec-driven development

Every `.py` module in `src/tradinglab/` has a colocated `.spec.md` file documenting purpose, public API, design decisions, and recent history. When you change a module's behavior, update its spec in the same PR. The catalog is `docs/SPEC_INDEX.md`.

## Smoke tests

`tests/smoke/test_smoke_full.py` is the authoritative acceptance suite — 54 `check_*` functions, each tied to a spec section. Add a new `check_*` for any user-visible behavior you introduce or fix. Naming: `check_<group><number>_<short_name>` (e.g. `check_d35_*`).

Run locally:

```bash
pytest tests/smoke -v
```

## GUI dialogs

**Combobox change handlers must be idempotent.** A `ttk.Combobox`
handler bound to `<<ComboboxSelected>>` / `<FocusOut>` that rebuilds
widgets (or re-themes the window) must short-circuit when the resolved
value is unchanged. Windows ttk fires `<FocusOut>` just from posting or
dismissing a dropdown popdown, and re-picking the current item fires
`<<ComboboxSelected>>`; a non-idempotent handler rebuilds on those no-op
events and the window visibly **flickers when you click the dropdown**.
Track the rendered value (e.g. `applied_kind_id`, `_rendered_mode`,
`_rendered_view_mode`) and return early when it matches. Reference
pattern: `IndicatorDialog._on_kind_changed`.

This rule is enforced codebase-wide by
`tests/unit/gui/test_dialog_combobox_no_flicker.py`: it builds each
combobox-bearing editor dialog, fires value-preserving combobox events
on every `ttk.Combobox`, and asserts the widget tree is not torn down +
recreated. **Add new combobox-bearing dialogs to its `DIALOG_BUILDERS`
registry.**

## Style

- `ruff check src tests` must pass
- Match existing patterns; prefer surgical edits over refactors
- No new dependencies without discussion in an issue first

## Pull requests

- One logical change per PR
- Update the relevant `.spec.md` in the same PR
- Reference the issue (if any) and the spec section in the description
