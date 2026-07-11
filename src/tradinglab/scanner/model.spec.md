# scanner/model.py — spec

## Purpose

Pure-data dataclasses for one saved scan. No registry lookups, no
indicator math, no Tk. The model layer round-trips through JSON and
structurally validates tree shape; the engine validates semantic
correctness against the registry.

## Public types

- `FieldRef(kind: "builtin"|"indicator"|"literal"|"expression", id, params, output_key, value, interval, symbol, terms)` — single value reference. `kind="expression"` carries an ordered infix `terms` list (see below) instead of an id / value.
- `ExprToken(kind: "operand"|"op", operand: FieldRef|None, op)` — one token of an expression: an operand wrapping a nested `FieldRef` leaf (field / indicator — incl. custom — / literal), or a binary operator (`+ - * / % **`) / parenthesis (`( )`).
- `validate_expression(terms) -> (ok, reason)` — structural check (operand/operator alternation + balanced parens). `evaluate_expression(terms, resolve) -> Optional[float]` — fold the infix stack to a scalar via a caller-supplied per-operand `resolve` callback; returns `None` on any `None` operand (Kleene), divide/modulo-by-zero, non-finite, or malformed input. Precedence: `**` (right-assoc) `> * / % > + -`.
- `Condition(left, op, params, interval, enabled, id, comment,
  within_last_bars, within_last_mode)` — leaf comparison.
- `Group(combinator: "and"|"or", children, enabled, id,
  within_last_bars, within_last_mode)` — internal node.
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

## FieldRef.interval — cross-interval

`FieldRef.interval` is persisted and supported when the caller provides
an `EvaluationContext.bars_registry`. The engine resolves the field via
that registry and returns `None` when the requested view is missing.
Without a registry, non-null overrides still hit the historical
`NotImplementedError` gate.

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

## Within-last-N-bars modifier

`Condition` and `Group` both persist `within_last_bars` (default `0`)
and `within_last_mode` (`"any"`, `"all"`, `"exactly"`). Non-defaults
make the engine walk the look-back window and emit `MatchEvidence`
when a node fires in the past. Defaults are omitted from JSON so legacy
scans round-trip unchanged.

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
