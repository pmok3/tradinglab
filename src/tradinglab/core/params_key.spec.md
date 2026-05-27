# params_key.py — Spec

## Purpose
Provide a single canonical helper for turning a per-indicator
``params`` mapping into a hashable, deterministic cache key. Replaces
two near-identical (but subtly drifting) ``_freeze_params`` copies in
``scanner/engine.py`` and ``strategy_tester/warmup.py``. Any future
indicator-keyed cache (custom-indicator preview, indicator screenshot
cache, etc.) reuses this helper instead of forking a third copy.

## Public API
- ``ParamsKey`` — type alias for ``tuple[tuple[str, Any], ...]``.
- ``freeze_params(p: Mapping[str, Any] | None) -> ParamsKey`` —
  recursive freezer; sorted by key. ``None`` and ``{}`` both collapse
  to ``()``.
- ``_freeze_value(v: Any) -> Any`` — internal; handles
  ``list``/``tuple`` → ``tuple``, ``dict`` → sorted tuple of pairs,
  ``set``/``frozenset`` → ``frozenset``. Other values pass through.

## Dependencies
- Internal: none.
- External: ``collections.abc.Mapping`` (stdlib).

## Design Decisions
- **Take the union of both prior implementations.** Scanner engine's
  version was 3 lines and ignored container values (would raise
  ``TypeError`` if any param was a list/dict). Warmup's version
  handled the common cases. The canonical helper recurses through
  list/tuple/dict/set so a future plugin's container-valued params
  don't surprise either cache site.
- **Coerce keys to ``str``.** Lets the cache stay robust against the
  rare plugin that uses non-string keys (e.g. an Enum). Matches the
  prior warmup behaviour.
- **Recurse on values, not just one level deep.** Nested containers
  (``{"levels": [{"weight": 1.0}, {"weight": 0.5}]}``) are fully
  flattened to hashable tuples — the prior implementations would have
  raised on this.
- **Don't pre-validate hashability of the leaf.** A non-hashable
  scalar (e.g. a custom class without ``__hash__``) will surface a
  ``TypeError`` at first use — same failure timing as the prior
  implementations. The helper isn't in the business of catching
  programmer error early.

## Invariants
- ``freeze_params(None) == freeze_params({}) == ()``.
- ``freeze_params({"a": 1, "b": 2}) == freeze_params({"b": 2, "a": 1})``
  (key-order-independent).
- The output is always a tuple of (str, hashable) pairs sorted by the
  string key.

## Testing
- ``tests/core/test_params_key.py`` — exhaustive: empty/None, scalar
  values, nested list, nested dict, sorted-by-key, str-key coercion,
  determinism across dict insertion orders.
