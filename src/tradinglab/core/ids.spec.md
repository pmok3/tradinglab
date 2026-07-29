# ids.py — Spec

## Purpose
Provide the single source of truth for minting generated record IDs, so the
per-subsystem `_new_id()` copies stop drifting. Five subsystems (`entries`,
`exits`, `scanner`, `strategy_tester`, `positions`) each shipped their own
one-line helper; the copies had already diverged into **two incompatible
on-disk formats**.

## Public API
- `new_id_hex() -> str` — dash-less 32-char `uuid4().hex`.
  On-disk format for `entries`, `exits`, `strategy_tester`.
- `new_id_dashed() -> str` — canonical dashed 36-char `str(uuid4())`.
  On-disk format for `scanner`, `positions`.

## Dependencies
- Internal: none.
- External: `uuid` (stdlib).

## Design Decisions
- **Both formats are exposed; neither is deprecated.** IDs are persisted
  inside saved strategies, scans, runs, and open-position blobs, and are
  cross-referenced between records. Collapsing to one spelling would orphan
  every existing saved file. The duplication being removed is the repeated
  *implementation*, not the format divergence — the format is now an
  explicit, named choice at each call site rather than an accident of which
  module the code was copy-pasted from.
- **Call sites keep their module-level `_new_id` alias.** `scanner/model.py`
  documents its helper as "centralized so tests can monkeypatch"; keeping the
  thin per-module delegator preserves that patch seam (and every other
  module-global lookup) while the body moves here.
- **No `new_id()` convenience default.** An unqualified name would let a new
  call site pick a format by accident — exactly the failure this module
  exists to prevent.

## Consumers
- `entries/model.py::_new_id` -> `new_id_hex`
- `exits/model.py::_new_id` -> `new_id_hex`
- `strategy_tester/model.py::_new_id` -> `new_id_hex`
- `scanner/model.py::_new_id` -> `new_id_dashed`
- `positions/tracker.py::_new_id` -> `new_id_dashed`

## Testing
- `tests/core/test_ids.py` — format shape (length, dash presence,
  `uuid.UUID` round-trip), uniqueness across repeated calls, and a pin that
  the two helpers stay distinct spellings of the same UUID4 source.

## See also
- [timezones](timezones.spec.md) — the sibling "one source of truth" helper
  for UTC/ET timestamp minting.
