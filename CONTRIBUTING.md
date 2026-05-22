# Contributing

## Spec-driven development

Every `.py` module in `src/tradinglab/` has a colocated `.spec.md` file documenting purpose, public API, design decisions, and recent history. When you change a module's behavior, update its spec in the same PR. The catalog is `docs/SPEC_INDEX.md`.

## Smoke tests

`tests/smoke/test_smoke_full.py` is the authoritative acceptance suite — 54 `check_*` functions, each tied to a spec section. Add a new `check_*` for any user-visible behavior you introduce or fix. Naming: `check_<group><number>_<short_name>` (e.g. `check_d35_*`).

Run locally:

```bash
pytest tests/smoke -v
```

## Style

- `ruff check src tests` must pass
- Match existing patterns; prefer surgical edits over refactors
- No new dependencies without discussion in an issue first

## Pull requests

- One logical change per PR
- Update the relevant `.spec.md` in the same PR
- Reference the issue (if any) and the spec section in the description
