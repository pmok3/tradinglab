# templates/__init__.py — Spec

## Purpose
Subpackage marker that re-exports the public API of `templates/seed.py` for the startup template-seeding flow (additive per-template ledger).

## Public API
Re-exported from `.seed`:
- `bundled_templates_dir`
- `seed_default_templates`
- `seed_default_templates_if_empty`

See `templates/seed.spec.md` for the authoritative API + semantics.

## Dependencies
- Internal: `.seed`.

