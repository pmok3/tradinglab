# scanner/model.py — spec

## Purpose

Pure-data dataclasses for one saved scan. No registry lookups, no
indicator math, no Tk. The model layer round-trips through JSON and
structurally validates tree shape; the engine validates semantic
correctness against the registry.

## Public types

- `FieldRef(kind: "builtin"|"indicator"|"literal", id, params, output_key, value, interval, symbol)` — single value reference.
- `Condition(left, op, params, interval, enabled, id, comment)` — leaf comparison.
- `Group(combinator: "and"|"or", children, enabled, id)` — internal node.
- `UniverseFilter(kind: "all"|"watchlist"|"symbols", name, symbols)` — symbol-set restriction.
- `OutputColumn(kind: "condition_value"|"field", ...)` — Treeview column descriptor.
- `ScanOptions(show_insufficient_data_rows, default_view: "new"|"active", new_view_capacity, extra)`.
- `CreatedWith(app, version)` — audit metadata.
- `ScanDefinition(name, root, primary_interval, universe_filter, output_columns, options, rank_by, rank_dir, rank_interval, schema_version, id, created_with, created_at, updated_at)`.
- `MatchEvidence(symbol, ts, primary_value, by_condition, by_field)` — per-match audit; `to_dict`/`from_dict`.
- `operator_param_schema(op) -> Tuple[Tuple[str, str], ...]` — lookup from `OPERATOR_PARAM_SCHEMA`. Used by `Condition.__post_init__` and the block-editor GUI.

## Operator schemas

Each operator declares `(param_name, param_kind)` in
`OPERATOR_PARAM_SCHEMA`. `param_kind ∈ {"field", "int", "float"}`.
Adding an operator: add the string id, add to `OPERATOR_PARAM_SCHEMA`,
implement in the engine.

Model enforces `Condition.params` keys match the schema **exactly**.
Value kinds are the engine's responsibility.

## Schema versioning

`"schema_version": 1`. `migrate(d, from_version)` chains forward;
raises if `from_version > SCHEMA_VERSION` (loud refusal beats silent
drift).

## What we *don't* do here

- Resolve `FieldRef.id` against the indicator registry — `fields.py`.
- Validate value types — engine.
- Compute anything — engine.
- Touch disk — `storage.py`.

## Stable IDs

Every `Condition`, `Group`, `OutputColumn`, `ScanDefinition` has a
UUID4 `id` minted at construction. Persisted so Treeview columns
survive rename / operator-swap, `MatchHistory` rings key on stable
ids, and reorder/duplicate doesn't break references.

`OutputColumn(kind="condition_value")` references `Condition.id`;
deleting the underlying condition causes the runner to emit the
column with `None` (engine returns `None` for unknown `condition_id`).

## FieldRef.interval — forward-compat

v1 scans always set `FieldRef.interval = None`. Engine raises
`NotImplementedError` on non-null override. Saving preserves the
slot; v2 will lift the restriction without re-migrating.

## FieldRef.symbol — cross-ticker pin

`FieldRef.symbol` defaults to `""` ("active symbol" — every legacy
saved scan deserialises to this). A non-empty value pins the ref to
that ticker; the engine resolves it against `(symbol, interval)` via
`EvaluationContext.bars_registry` (see `engine.spec.md` →
"FieldRef.symbol cross-symbol"). `FieldRef.is_cross_symbol()` returns
`True` iff `symbol` is non-empty.

`to_dict()` omits the `symbol` key when empty so existing saved JSON
round-trips byte-identically (no spurious `"symbol": ""` appearing on
previously-saved scans the first time they're re-saved). `from_dict`
accepts the missing key (defaults to `""`) — full back-compat for
every pre-Phase-1 scan / entry / exit JSON ever written.

## Within-pct semantics (engine, specced here so the spec is one place)

`|left - target| / abs(target) <= tolerance_pct / 100`. If
`abs(target) < 1e-12`, return `None`. `tolerance_pct` is a plain
`float` named param (not a FieldRef literal) — operator-only scalars
use plain numerics.

## ScanOptions.extra

Forward-compat round-trip only. Behavioral knobs MUST be typed
attributes on `ScanOptions`, not `extra` keys. Old saves load with
defaults; new saves write the typed value.

## Round-trip invariant

For every well-formed `ScanDefinition d`:

```
ScanDefinition.from_dict(d.to_dict()).to_dict() == d.to_dict()
```

(Modulo `created_at`/`updated_at` if `migrate()` chooses to update —
currently it doesn't.) Property-tested by the smoke suite.
