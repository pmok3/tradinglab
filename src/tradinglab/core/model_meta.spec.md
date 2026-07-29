# model_meta.py — Spec

## Purpose
Single definition of `CreatedWith`, the provenance metadata attached to
saved user records (entry strategies, exit strategies, scans). Replaces
three hand-rolled copies in `entries/model.py`, `exits/model.py` and
`scanner/model.py` that had already drifted apart.

## Public API
- `@dataclass CreatedWith(app="tradinglab", version="0.0.0", template=False)`
  - `to_dict() -> dict[str, Any]` — always emits `app` + `version`; emits
    `template` **only when True**.
  - `from_dict(Mapping) -> CreatedWith` — tolerant; missing keys fall back to
    the subclass's own field defaults (via `cls()`), so a subclass that
    overrides `version` keeps its default on load too.

## Dependencies
- Internal: none.
- External: `dataclasses`, `collections.abc.Mapping` (stdlib).

## Design Decisions
- **`template` is in the shared superset because omitting it lost data.**
  All 20 shipped exit templates carry `"created_with": {"template": true}`
  on disk, but the old `exits.CreatedWith` read only `app` and `version` —
  so `ExitStrategy.from_dict(raw).to_dict()` silently demoted a template to
  a non-template. This was a real round-trip data loss, not cosmetic drift.
- **`template` is omitted when False.** Records that never set it serialize
  byte-identically to before the consolidation, so no existing saved file
  changes shape.
- **Per-subsystem `version` defaults are preserved via subclassing.** Scans
  historically defaulted `version` to `""` while entries/exits used
  `"0.0.0"`. `scanner.model.CreatedWith` overrides the field rather than
  forcing a single default, because the default is what lands on disk for
  records saved without explicit provenance.
- **`from_dict` reads defaults from `cls()`, not hardcoded literals.**
  Otherwise the scanner subclass would silently inherit `"0.0.0"` on any
  load that omitted `version`.
- **Subclasses, not aliases.** Each subsystem re-exports a `CreatedWith`
  name so `from tradinglab.entries.model import CreatedWith` keeps working
  and `isinstance` checks stay subsystem-scoped.

## Invariants
- `CreatedWith().to_dict()` has no `template` key.
- `CreatedWith.from_dict(x.to_dict()) == x` for any instance `x`.
- `scanner.model.CreatedWith().version == ""`.
- `entries.model.CreatedWith().version == exits.model.CreatedWith().version == "0.0.0"`.
- Round-tripping any shipped template preserves `created_with.template`.

## Consumers
- `entries/model.py::CreatedWith`
- `exits/model.py::CreatedWith`
- `scanner/model.py::CreatedWith` (overrides `version` default)

## Testing
- `tests/core/test_model_meta.py` — omit-when-False serialization, round
  trip, per-subsystem default preservation, and a catalog sweep asserting
  every shipped entry/exit template keeps its `template` flag through
  `from_dict` -> `to_dict`.

## See also
- [ids](ids.spec.md) — sibling "one source of truth" helper for the
  per-subsystem `_new_id` copies.
