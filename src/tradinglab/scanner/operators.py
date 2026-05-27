"""Operator dispatch registry for :func:`scanner.engine._evaluate_condition_at`.

This module collapses the long ``if op == ...`` chain that previously
lived inside :func:`scanner.engine._evaluate_condition_at` into a single
:data:`OPERATOR_EVALUATORS` dict keyed by operator id.

Design notes
------------

Each entry is an :class:`OpHandler` bundling:

* ``evaluate(cond, ctx, i) -> bool | None`` — the per-operator predicate.
  The tri-valued (Kleene) contract from the engine is preserved: every
  handler returns ``None`` for any missing operand or insufficient
  history; otherwise ``True`` / ``False``.
* ``is_transition: bool`` — flag used by the centralized forming-bar
  guard in ``_evaluate_condition_at``. Transition ops (``crosses_above``,
  ``crosses_below``) MUST NOT fire on a forming bar inside a look-back
  walk per the trader-spec'd "transitions on closed bars only" rule.
  Comparison operators stay live regardless.

Circular-import avoidance
-------------------------

Per-op evaluators need :func:`scanner.engine.evaluate_field_at` (and
:func:`scanner.engine._is_nan_like`). To avoid an import cycle, this
module exposes module-level slots ``_evaluate_field_at`` and
``_is_nan_like`` which ``engine.py`` wires at module load (the bottom of
``engine.py`` does ``operators._evaluate_field_at = evaluate_field_at``).
Per-op handlers go through the small ``_ef`` / ``_nanlike`` shims so
the wiring is a single point of indirection.

Extending
---------

Add a new operator by:

1. Defining ``OP_FOO = "foo"`` and its param schema entry in ``model.py``.
2. Writing ``_eval_foo(cond, ctx, i) -> bool | None`` here.
3. Registering ``OPERATOR_EVALUATORS[OP_FOO] = OpHandler(_eval_foo)``
   (set ``is_transition=True`` only if the op participates in the
   forming-bar guard).
4. Adding the op to the registry-completeness test in
   ``tests/scanner/test_operators_registry.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from .model import (
    OP_BETWEEN,
    OP_CROSSES_ABOVE,
    OP_CROSSES_BELOW,
    OP_EQ,
    OP_GE,
    OP_GT,
    OP_HOLDING_ABOVE,
    OP_HOLDING_BELOW,
    OP_INSIDE_BAR,
    OP_IS_FALLING,
    OP_IS_RISING,
    OP_LE,
    OP_LT,
    OP_NE,
    OP_NEW_HIGH_N,
    OP_NEW_LOW_N,
    OP_NR7,
    OP_OUTSIDE_BAR,
    OP_WITHIN_PCT,
    Condition,
)

if TYPE_CHECKING:
    from .engine import EvaluationContext

# Late-bound by engine.py at module load to avoid a circular import.
# These remain None until engine.py finishes initialising; nothing in
# this module calls them at import time, so the wiring window is safe.
_evaluate_field_at: Callable[..., float | None] | None = None
_is_nan_like: Callable[[Any], bool] | None = None


def _ef(ref, ctx, i):  # noqa: ANN001
    # Tiny shim so per-op evaluators read like the original engine body.
    return _evaluate_field_at(ref, ctx, i)  # type: ignore[misc]


def _nanlike(x):  # noqa: ANN001
    return _is_nan_like(x)  # type: ignore[misc]


OpEvalFn = Callable[[Condition, "EvaluationContext", int], "bool | None"]


@dataclass(frozen=True)
class OpHandler:
    """Bundle of behaviour + metadata for a single scanner operator."""

    evaluate: OpEvalFn
    is_transition: bool = False


# ---------------------------------------------------------------------------
# Per-operator evaluators (one function each; bodies match the prior
# ``_evaluate_condition_at`` if/elif chain).
# ---------------------------------------------------------------------------


def _eval_gt(cond, ctx, i):
    l = _ef(cond.left, ctx, i)
    r = _ef(cond.params["right"], ctx, i)
    if l is None or r is None:
        return None
    return l > r


def _eval_lt(cond, ctx, i):
    l = _ef(cond.left, ctx, i)
    r = _ef(cond.params["right"], ctx, i)
    if l is None or r is None:
        return None
    return l < r


def _eval_ge(cond, ctx, i):
    l = _ef(cond.left, ctx, i)
    r = _ef(cond.params["right"], ctx, i)
    if l is None or r is None:
        return None
    return l >= r


def _eval_le(cond, ctx, i):
    l = _ef(cond.left, ctx, i)
    r = _ef(cond.params["right"], ctx, i)
    if l is None or r is None:
        return None
    return l <= r


def _eval_eq(cond, ctx, i):
    l = _ef(cond.left, ctx, i)
    r = _ef(cond.params["right"], ctx, i)
    if l is None or r is None:
        return None
    return l == r


def _eval_ne(cond, ctx, i):
    l = _ef(cond.left, ctx, i)
    r = _ef(cond.params["right"], ctx, i)
    if l is None or r is None:
        return None
    return l != r


def _eval_between(cond, ctx, i):
    l = _ef(cond.left, ctx, i)
    lo = _ef(cond.params["low"], ctx, i)
    hi = _ef(cond.params["high"], ctx, i)
    if l is None or lo is None or hi is None:
        return None
    return lo <= l <= hi


def _eval_crosses_above(cond, ctx, i):
    lookback = int(cond.params["lookback"])
    if lookback < 1 or i - lookback < 0:
        return None
    prev_l = _ef(cond.left, ctx, i - lookback)
    prev_r = _ef(cond.params["right"], ctx, i - lookback)
    cur_l = _ef(cond.left, ctx, i)
    cur_r = _ef(cond.params["right"], ctx, i)
    if None in (prev_l, prev_r, cur_l, cur_r):
        return None
    return prev_l <= prev_r and cur_l > cur_r


def _eval_crosses_below(cond, ctx, i):
    lookback = int(cond.params["lookback"])
    if lookback < 1 or i - lookback < 0:
        return None
    prev_l = _ef(cond.left, ctx, i - lookback)
    prev_r = _ef(cond.params["right"], ctx, i - lookback)
    cur_l = _ef(cond.left, ctx, i)
    cur_r = _ef(cond.params["right"], ctx, i)
    if None in (prev_l, prev_r, cur_l, cur_r):
        return None
    return prev_l >= prev_r and cur_l < cur_r


def _eval_is_rising(cond, ctx, i):
    lookback = int(cond.params["lookback"])
    if lookback < 1 or i - lookback < 0:
        return None
    vals = [_ef(cond.left, ctx, j) for j in range(i - lookback, i + 1)]
    if any(v is None for v in vals):
        return None
    return all(vals[k] < vals[k + 1] for k in range(len(vals) - 1))


def _eval_is_falling(cond, ctx, i):
    lookback = int(cond.params["lookback"])
    if lookback < 1 or i - lookback < 0:
        return None
    vals = [_ef(cond.left, ctx, j) for j in range(i - lookback, i + 1)]
    if any(v is None for v in vals):
        return None
    return all(vals[k] > vals[k + 1] for k in range(len(vals) - 1))


def _eval_within_pct(cond, ctx, i):
    l = _ef(cond.left, ctx, i)
    t = _ef(cond.params["target"], ctx, i)
    if l is None or t is None:
        return None
    if t == 0.0:
        return None  # zero-target → undefined per spec
    try:
        tol = float(cond.params["tolerance_pct"])
    except (TypeError, ValueError):
        return None
    return abs((l - t) / t) * 100.0 <= tol


def _eval_new_high_n(cond, ctx, i):
    n = int(cond.params["n"])
    if n < 1 or i - n < 0:
        return None
    cur = _ef(cond.left, ctx, i)
    if cur is None:
        return None
    prior = [_ef(cond.left, ctx, j) for j in range(i - n, i)]
    if any(v is None for v in prior):
        return None
    return cur > max(prior)


def _eval_new_low_n(cond, ctx, i):
    n = int(cond.params["n"])
    if n < 1 or i - n < 0:
        return None
    cur = _ef(cond.left, ctx, i)
    if cur is None:
        return None
    prior = [_ef(cond.left, ctx, j) for j in range(i - n, i)]
    if any(v is None for v in prior):
        return None
    return cur < min(prior)


def _eval_holding_above(cond, ctx, i):
    bars_n = int(cond.params["bars"])
    if bars_n < 1 or i - bars_n + 1 < 0:
        return None
    for j in range(i - bars_n + 1, i + 1):
        l = _ef(cond.left, ctx, j)
        r = _ef(cond.params["reference"], ctx, j)
        if l is None or r is None:
            return None
        if not (l > r):
            return False
    return True


def _eval_holding_below(cond, ctx, i):
    bars_n = int(cond.params["bars"])
    if bars_n < 1 or i - bars_n + 1 < 0:
        return None
    for j in range(i - bars_n + 1, i + 1):
        l = _ef(cond.left, ctx, j)
        r = _ef(cond.params["reference"], ctx, j)
        if l is None or r is None:
            return None
        if not (l < r):
            return False
    return True


def _eval_inside_bar(cond, ctx, i):
    if i < 1:
        return None
    bars = ctx.bars
    h_now, h_prev = bars.high[i], bars.high[i - 1]
    l_now, l_prev = bars.low[i], bars.low[i - 1]
    if any(_nanlike(x) for x in (h_now, h_prev, l_now, l_prev)):
        return None
    return bool(h_now < h_prev and l_now > l_prev)


def _eval_outside_bar(cond, ctx, i):
    if i < 1:
        return None
    bars = ctx.bars
    h_now, h_prev = bars.high[i], bars.high[i - 1]
    l_now, l_prev = bars.low[i], bars.low[i - 1]
    if any(_nanlike(x) for x in (h_now, h_prev, l_now, l_prev)):
        return None
    return bool(h_now > h_prev and l_now < l_prev)


def _eval_nr7(cond, ctx, i):
    if i < 6:
        return None
    bars = ctx.bars
    ranges = bars.high[i - 6 : i + 1] - bars.low[i - 6 : i + 1]
    if np.any(np.isnan(ranges)):
        return None
    cur_range = float(ranges[-1])
    prior_min = float(ranges[:-1].min())
    return bool(cur_range <= prior_min)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


OPERATOR_EVALUATORS: dict[str, OpHandler] = {
    OP_GT: OpHandler(_eval_gt),
    OP_LT: OpHandler(_eval_lt),
    OP_GE: OpHandler(_eval_ge),
    OP_LE: OpHandler(_eval_le),
    OP_EQ: OpHandler(_eval_eq),
    OP_NE: OpHandler(_eval_ne),
    OP_BETWEEN: OpHandler(_eval_between),
    OP_CROSSES_ABOVE: OpHandler(_eval_crosses_above, is_transition=True),
    OP_CROSSES_BELOW: OpHandler(_eval_crosses_below, is_transition=True),
    OP_IS_RISING: OpHandler(_eval_is_rising),
    OP_IS_FALLING: OpHandler(_eval_is_falling),
    OP_WITHIN_PCT: OpHandler(_eval_within_pct),
    OP_NEW_HIGH_N: OpHandler(_eval_new_high_n),
    OP_NEW_LOW_N: OpHandler(_eval_new_low_n),
    OP_HOLDING_ABOVE: OpHandler(_eval_holding_above),
    OP_HOLDING_BELOW: OpHandler(_eval_holding_below),
    OP_INSIDE_BAR: OpHandler(_eval_inside_bar),
    OP_OUTSIDE_BAR: OpHandler(_eval_outside_bar),
    OP_NR7: OpHandler(_eval_nr7),
}


def register_op(name: str, handler: OpHandler) -> None:
    """Register (or replace) a handler for operator ``name``.

    Intended for tests + plugins. Keeps :data:`OPERATOR_EVALUATORS` as
    the single source of truth. Note: callers that mutate the registry
    in tests should save/restore the prior handler in a ``try/finally``.
    """
    OPERATOR_EVALUATORS[name] = handler


#: Back-compat re-export. Derived view of the registry; preserved so any
#: external code that imported ``_TRANSITION_OPS`` from ``engine``
#: continues to see the same membership.
TRANSITION_OPS: frozenset[str] = frozenset(
    op for op, h in OPERATOR_EVALUATORS.items() if h.is_transition
)


__all__ = [
    "OpHandler",
    "OpEvalFn",
    "OPERATOR_EVALUATORS",
    "TRANSITION_OPS",
    "register_op",
]
