"""Deterministic hashable key for an indicator-params mapping.

Used by per-process caches keyed on ``(kind_id, frozen_params)``:

* :mod:`tradinglab.scanner.engine` ``IndicatorMemo.get`` — caches one
  indicator ``compute_arr`` result per kind+params pair for the
  lifetime of a scanner evaluation.
* :mod:`tradinglab.strategy_tester.warmup` ``warmup_bars_for_kind`` —
  caches the empirical warmup-bar count per kind+params pair.

Any future indicator-keyed cache (custom-indicator preview, indicator
screenshot cache, etc.) should reuse this helper rather than copy-
pasting another ``_freeze_params`` variant.

Hashability contract: the returned key is a ``tuple`` of ``(str,
hashable_value)`` pairs sorted by key. Containers nested in the
params dict (``list`` / ``dict``) are recursively frozen into
hashable shapes — see :func:`freeze_params` for the rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ParamsKey = tuple[tuple[str, Any], ...]


def _freeze_value(v: Any) -> Any:
    """Convert a single value to a hashable form, recursively.

    * ``list`` / ``tuple`` → tuple of frozen elements (preserves order
      because list-typed params are usually positional, e.g. moving-
      average length sequences).
    * ``dict`` → tuple of ``(str(key), frozen_value)`` pairs sorted by
      key.
    * ``set`` / ``frozenset`` → ``frozenset`` of frozen elements.
    * Any other value passes through unchanged. Non-hashable scalars
      will surface a ``TypeError`` only when the returned key is
      actually used in a dict — same failure timing as before.
    """
    if isinstance(v, list | tuple):
        return tuple(_freeze_value(x) for x in v)
    if isinstance(v, dict):
        return tuple(sorted(
            (str(k), _freeze_value(val)) for k, val in v.items()
        ))
    if isinstance(v, set | frozenset):
        return frozenset(_freeze_value(x) for x in v)
    return v


def freeze_params(p: Mapping[str, Any] | None) -> ParamsKey:
    """Hashable, deterministic key for a params dict.

    Empty/``None`` collapses to ``()``. Keys are coerced to ``str`` to
    tolerate the rare plugin that uses non-string keys; values are
    recursively frozen via :func:`_freeze_value`. The resulting tuple
    is sorted by key so two dicts with the same content produce the
    same key regardless of insertion order.

    Examples:

        freeze_params({"length": 14, "source": "close"})
        # → (("length", 14), ("source", "close"))

        freeze_params({"levels": [20, 50, 80]})
        # → (("levels", (20, 50, 80)))

        freeze_params(None) == freeze_params({}) == ()
    """
    if not p:
        return ()
    return tuple(sorted(
        (str(k), _freeze_value(v)) for k, v in p.items()
    ))
