"""Scanner engine: tri-valued evaluation of ``ScanDefinition`` against bars.

This module is **pure**: no Tk, no I/O, no threading. Given a
:class:`~tradinglab.scanner.model.ScanDefinition` and an
:class:`EvaluationContext` (one symbol, one interval, one bar index),
:func:`evaluate_scan` returns ``True`` / ``False`` / ``None``.

Tri-valued (Kleene) logic
-------------------------

Every operator can return ``None`` when any operand or required
historical bar is missing (NaN, out-of-bounds, indicator not yet
populated). Group combinators propagate ``None`` like SQL nulls:

- ``AND``: any child ``False`` → ``False``; all ``True`` → ``True``;
  else ``None``.
- ``OR``: any child ``True`` → ``True``; all ``False`` → ``False``;
  else ``None``.

Disabled children are *skipped* (not contributed as ``None``); a group
containing only disabled children returns ``None``.

Caching
-------

Indicator output dicts (``Indicator.compute()`` returns
``Dict[str, np.ndarray]``) are computed once per ``(kind_id,
frozen_params)`` per symbol and cached on the :class:`IndicatorMemo`
hung off the context. Built-in scalars are cheap and re-evaluated each
call. The runner layer (``scanner/runner.py``) reuses one memo across
multiple scans on the same symbol/interval/tick — the SWE critique's
"runner-scope memo" optimization.

Forward-compat
--------------

``FieldRef.interval`` overrides are persisted but raise
``NotImplementedError`` here in v1. ``Condition.interval`` overrides
that don't match the context's interval evaluate to ``None``
(intentionally silent — mixed-interval scans are deferred to v2).

Errors during indicator compute are caught, logged, and recorded on
``IndicatorMemo.errors`` so the GUI can surface them per-symbol; the
field then resolves to ``None``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

import numpy as np

from ..indicators.base import compute_via_bars, factory_by_kind_id
from ..models import Candle
from .fields import (
    BarsNp,
    builtin_compute,
    condition_uses_daily_reset_field,
    get_field,
    validate_field_ref,
)
from .model import (
    FIELD_KIND_BUILTIN,
    FIELD_KIND_INDICATOR,
    FIELD_KIND_LITERAL,
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
    WITHIN_LAST_MODE_ALL,
    WITHIN_LAST_MODE_EXACTLY,
    Condition,
    FieldRef,
    Group,
    MatchEvidence,
    ScanDefinition,
)
from .session import find_session_open_index

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indicator memo
# ---------------------------------------------------------------------------


def _freeze_params(p: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Hashable, deterministic key for an indicator's params dict."""
    return tuple(sorted((str(k), v) for k, v in (p or {}).items()))


@dataclass
class IndicatorMemo:
    """Cache of indicator ``compute()`` results for a single bar series.

    Built lazily: the first time a ``(kind_id, params)`` pair is asked
    for, we instantiate the factory, call ``compute()``, and stash the
    output dict. Subsequent lookups (any output_key, any index) hit the
    cache.

    On compute exceptions, the memo records the error in ``errors`` and
    caches an empty dict so we don't keep retrying a broken indicator.

    Incremental protocol
    --------------------

    Indicators that implement ``inc_init`` / ``inc_step`` are
    incrementally advanceable on closed-bar appends: rather than
    discarding the cache when bars grow by one, the runner can call
    :meth:`advance_for_append` to extend the cached output in place
    (typically O(L) per indicator instead of O(n)). State for each
    incremental indicator is kept in ``_inc_states``, keyed identically
    to ``cache``. Indicator instances are retained in ``_instances`` so
    the same instance — including its committed state — persists
    across ticks.

    Forming-bar updates do NOT use the incremental path in this slice;
    the runner's forming reconcile branch rebuilds the memo. Any
    indicator that doesn't support the incremental protocol (or whose
    ``inc_step`` raises) silently falls back to a per-key drop +
    recompute via :meth:`get` on the next access.
    """

    candles: list[Candle]
    cache: dict[tuple[str, tuple[tuple[str, Any], ...]], dict[str, np.ndarray]] = (
        dc_field(default_factory=dict)
    )
    errors: dict[str, str] = dc_field(default_factory=dict)
    # Lazy ``Bars`` view shared across all indicators on this symbol.
    # Built once on first ``get`` call so N indicators share one column
    # extraction. Stays ``None`` if no indicator is ever requested.
    _bars: Any | None = dc_field(default=None, repr=False)
    # Retained instances (one per cache key) so incremental advance
    # can call back into them across ticks.
    _instances: dict[tuple[str, tuple[tuple[str, Any], ...]], Any] = dc_field(
        default_factory=dict, repr=False,
    )
    # Per-key incremental state. Populated by ``get`` for indicators
    # that implement ``inc_init``; advanced by :meth:`advance_for_append`.
    _inc_states: dict[tuple[str, tuple[tuple[str, Any], ...]], dict[str, Any]] = dc_field(
        default_factory=dict, repr=False,
    )

    def _get_bars(self):
        if self._bars is None:
            from ..core.bars import Bars
            self._bars = Bars.from_candles(self.candles)
        return self._bars

    def get(self, kind_id: str, params: Mapping[str, Any]) -> dict[str, np.ndarray]:
        key = (kind_id, _freeze_params(params))
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        entry = factory_by_kind_id(kind_id)
        if entry is None:
            self.errors[kind_id] = "indicator not registered"
            self.cache[key] = {}
            return self.cache[key]
        _name, factory = entry
        try:
            inst = factory(**dict(params or {}))
            bars = self._get_bars()
            out = compute_via_bars(inst, bars)
        except Exception as e:  # noqa: BLE001
            LOG.exception("indicator %s(%s) compute failed", kind_id, dict(params or {}))
            self.errors[kind_id] = repr(e)
            self.cache[key] = {}
            return self.cache[key]
        if not isinstance(out, dict):
            self.errors[kind_id] = (
                f"compute() returned {type(out).__name__}, expected dict"
            )
            self.cache[key] = {}
            return self.cache[key]
        self.cache[key] = out
        self._instances[key] = inst
        # If the indicator supports the incremental protocol, seed its
        # state now so subsequent appends can advance cheaply. Failures
        # here are silent — full compute already produced the output.
        inc_init = getattr(inst, "inc_init", None)
        if callable(inc_init):
            try:
                self._inc_states[key] = inc_init(bars)
            except Exception:  # noqa: BLE001
                LOG.exception("inc_init failed for %s; will fall back to recompute", kind_id)
        return out

    def advance_for_append(
        self,
        new_bars: Any,
        *,
        prev_len: int,
        stats_sink: dict[str, int] | None = None,
    ) -> None:
        """Advance every cached indicator by ``len(new_bars) - prev_len`` closed bars.

        Indicators that support the incremental protocol step
        in-place. Indicators that do not (or whose ``inc_step``
        raises) are dropped from the cache, forcing a fresh compute on
        the next :meth:`get` call. The caller is responsible for
        updating ``self.candles`` to the new list AFTER this returns.

        ``stats_sink`` (optional) accumulates ``incremental_steps`` /
        ``incremental_falls_back`` counters — used by the runner to
        surface reuse vs rebuild ratios via :meth:`stats`.
        """
        if stats_sink is None:
            stats_sink = {}
        for key in list(self.cache.keys()):
            inst = self._instances.get(key)
            state = self._inc_states.get(key)
            inc_step = getattr(inst, "inc_step", None) if inst is not None else None
            if inst is not None and state is not None and callable(inc_step):
                try:
                    new_state = inc_step(state, new_bars, prev_len=prev_len)
                except Exception:  # noqa: BLE001
                    LOG.exception("inc_step failed for %s; dropping cache entry", key)
                else:
                    if isinstance(new_state, dict) and isinstance(
                        new_state.get("output"), dict
                    ):
                        self._inc_states[key] = new_state
                        self.cache[key] = new_state["output"]
                        stats_sink["incremental_steps"] = (
                            stats_sink.get("incremental_steps", 0) + 1
                        )
                        continue
                    LOG.error("inc_step for %s returned malformed state; dropping", key)
            # Fall back: drop entry; .get() recomputes on next access.
            self.cache.pop(key, None)
            self._instances.pop(key, None)
            self._inc_states.pop(key, None)
            stats_sink["incremental_falls_back"] = (
                stats_sink.get("incremental_falls_back", 0) + 1
            )
        self._bars = new_bars


# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------


@dataclass
class EvaluationContext:
    """One symbol, one interval, one current-index, one indicator memo.

    Constructed once per symbol per tick. ``bars`` is the snapshotted
    NumPy view (used by built-ins and by structural operators);
    ``candles`` is the original list (handed to indicator
    ``compute()``); ``current_index`` is the bar index "now".

    ``bars_registry`` (optional) is the
    :class:`tradinglab.core.bars_registry.BarsRegistry` used to
    resolve cross-interval :class:`FieldRef` / :class:`Condition`
    references. When non-None, a field whose ``interval`` differs from
    this context's ``interval`` is evaluated against the bars/memo for
    ``(symbol, ref.interval)`` pulled from the registry. When None,
    the historical ``NotImplementedError`` gate is preserved for
    back-compat with v1 callers.
    """

    symbol: str
    interval: str
    bars: BarsNp
    candles: list[Candle]
    current_index: int
    memo: IndicatorMemo = dc_field(default_factory=lambda: IndicatorMemo(candles=[]))
    bars_registry: Any | None = None
    # ``True`` if the bar at ``current_index`` is still forming (intra-bar
    # tick before bar close). The within-last-N-bars walk uses this to
    # skip transition operators (``crosses_above`` / ``crosses_below``)
    # at the forming bar — wick whipsaws would otherwise spuriously
    # trigger look-back conditions for the rest of the bar's lifetime.
    # Comparisons stay live regardless of this flag (per trader spec).
    # Default ``False`` preserves today's behavior; the runner sets this
    # when emitting forming-bar updates.
    is_forming: bool = False
    # Per-evaluation collector for :class:`MatchEvidence` payloads
    # produced by within-last-N-bars walks. The walk appends one entry
    # per matched look-back leaf (Condition / Group with
    # ``within_last_bars > 0`` that evaluates True). Callers that want
    # evidence reset this list before evaluating a Scan; callers that
    # ignore evidence pay only the cost of the empty-list append guard.
    evidence: list[MatchEvidence] = dc_field(default_factory=list)


# Transition operators — gated by the forming-bar skip rule when the
# within-last-N-bars walk is active. Module-level so the gate test is
# a single set lookup.
_TRANSITION_OPS = frozenset({OP_CROSSES_ABOVE, OP_CROSSES_BELOW})


def make_context(
    symbol: str,
    interval: str,
    candles: list[Candle],
    current_index: int | None = None,
    memo: IndicatorMemo | None = None,
    bars: BarsNp | None = None,
    bars_registry: Any | None = None,
    is_forming: bool = False,
) -> EvaluationContext:
    """Construct a fresh context with snapshot + memo.

    If ``current_index`` is not given, defaults to the last bar.
    If ``memo`` is not given, a new memo bound to ``candles`` is made;
    pass an existing memo to share indicator results across scans on the
    same symbol/interval/tick (runner-scope memo).

    If ``bars`` is provided, it is used directly as ``ctx.bars`` instead
    of rebuilding via :meth:`BarsNp.from_candles` (the perf seam used
    by the runner's per-symbol ``BarsBuffer``). It MUST satisfy
    ``len(bars) == len(candles)``; mismatch raises ``ValueError`` to
    surface invariant violations rather than silently masking them.
    The provided ``bars`` is also bound onto the memo as ``_bars`` so
    that indicator computes (which run via
    :func:`compute_via_bars`) reuse the same view rather than building
    their own.

    ``is_forming`` (default ``False``) flags the bar at ``current_index``
    as still-forming (intra-bar tick). The within-last-N-bars walk uses
    this to skip transition operators on the forming bar; it is
    otherwise ignored so existing callers see no behavior change.
    """
    if bars is not None:
        if len(bars) != len(candles):
            raise ValueError(
                f"make_context: bars length {len(bars)} does not match "
                f"candles length {len(candles)} (symbol={symbol!r})"
            )
    if bars is None:
        bars = BarsNp.from_candles(candles)
    if current_index is None:
        current_index = max(0, len(candles) - 1)
    if memo is None:
        memo = IndicatorMemo(candles=candles)
    elif memo.candles is not candles:
        # Different candle list → different cache. Safer to clear both
        # the indicator-output cache AND the lazy ``Bars`` view (the
        # latter previously leaked across rebinds — fixed here).
        # Incremental state is keyed to the prior bars, so clear it
        # too — the next ``get`` will rebuild from scratch.
        memo.candles = candles
        memo.cache.clear()
        memo.errors.clear()
        memo._bars = None
        memo._instances.clear()
        memo._inc_states.clear()
    # Bind the provided bars onto the memo so indicator compute paths
    # share the runner's view instead of rebuilding their own.
    memo._bars = bars
    return EvaluationContext(
        symbol=symbol.upper(),
        interval=interval,
        bars=bars,
        candles=candles,
        current_index=current_index,
        memo=memo,
        bars_registry=bars_registry,
        is_forming=is_forming,
    )


# ---------------------------------------------------------------------------
# Field evaluation
# ---------------------------------------------------------------------------


def _coerce_float(v: Any) -> float | None:
    """Best-effort conversion to a finite Python float; None on failure/NaN/inf."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _is_nan_like(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return True
    return math.isnan(f) or math.isinf(f)


def evaluate_field_at(
    ref: FieldRef, ctx: EvaluationContext, index: int
) -> float | None:
    """Resolve ``ref`` at the given bar index. Returns ``None`` for OOB / NaN.

    Cross-interval semantics: if ``ref.interval`` is non-null and
    differs from ``ctx.interval``, the engine tries to resolve the
    field against the bars/memo for ``(symbol, ref.interval)`` from
    ``ctx.bars_registry``. If no registry is bound to the context,
    raises :class:`NotImplementedError` (the v1 back-compat gate).
    If the registry has no buffer for that key yet (lazy-load
    pending), the field resolves to ``None``.
    """
    if ref.interval is not None and ref.interval != ctx.interval:
        if ctx.bars_registry is None:
            raise NotImplementedError(
                f"FieldRef.interval override ({ref.interval!r}) requires a "
                f"BarsRegistry on the EvaluationContext; current scan runs at "
                f"interval {ctx.interval!r} with no registry bound"
            )
        # Resolve via the cross-interval registry. The ``index``
        # argument is in this context's bar space; for the alternate
        # interval we re-target to that buffer's last bar (since
        # cross-interval references don't have a meaningful shared
        # index — see the spec for why "now" semantics is the only
        # sane v1 interpretation).
        sub_ctx = _sub_context_for_interval(ctx, ref.interval)
        if sub_ctx is None:
            return None
        # Build a copy of the ref with ``interval=None`` so the
        # recursive call resolves locally against the sub-context.
        local_ref = _strip_interval(ref)
        # Use the sub-context's last index when index points "now";
        # for lookback ops that pass i-N, we map proportionally —
        # but for v1, simply use the sub-context's current_index +
        # (index - ctx.current_index) clamped to valid range.
        offset = index - ctx.current_index
        sub_index = sub_ctx.current_index + offset
        return evaluate_field_at(local_ref, sub_ctx, sub_index)
    if ref.kind == FIELD_KIND_LITERAL:
        return _coerce_float(ref.value)
    if index < 0 or index >= len(ctx.bars):
        return None
    if ref.kind == FIELD_KIND_BUILTIN:
        fn = builtin_compute(ref.id)
        if fn is None:
            return None
        try:
            v = fn(ctx.bars, index, ref.params or {})
        except Exception:  # noqa: BLE001
            LOG.exception("builtin %s failed at index %d", ref.id, index)
            return None
        return _coerce_float(v)
    if ref.kind == FIELD_KIND_INDICATOR:
        out = ctx.memo.get(ref.id, ref.params or {})
        if not out:
            return None
        key = ref.output_key
        if not key:
            spec = get_field(ref.id, kind="indicator")
            if spec is None:
                return None
            key = spec.default_output_key
        arr = out.get(key)
        if arr is None:
            return None
        if index >= len(arr):
            return None
        return _coerce_float(arr[index])
    return None


def _strip_interval(ref: FieldRef) -> FieldRef:
    """Return a copy of ``ref`` with ``interval`` cleared.

    Used internally when redirecting a cross-interval reference to a
    sub-context whose ``interval`` already matches the ref's target —
    the recursive ``evaluate_field_at`` call must see ``interval=None``
    to skip the cross-interval branch.
    """
    if ref.interval is None:
        return ref
    if ref.kind == FIELD_KIND_LITERAL:
        return FieldRef(kind=ref.kind, value=ref.value, interval=None)
    return FieldRef(
        kind=ref.kind,
        id=ref.id,
        params=dict(ref.params or {}),
        output_key=ref.output_key,
        interval=None,
    )


def _sub_context_for_interval(
    ctx: EvaluationContext, interval: str
) -> EvaluationContext | None:
    """Build a sibling context against the bars/memo for ``interval``.

    Pulls the :class:`BarsView` for ``(ctx.symbol, interval)`` from
    ``ctx.bars_registry``; returns ``None`` if the registry has no
    buffer yet (lazy-load in flight). The returned context shares
    ``ctx.bars_registry`` so nested cross-interval lookups still
    work.

    The sub-context's ``candles`` list is the memo's own list (not a
    copy) so the engine's ``make_context``-style "different list
    means clear the cache" guard is naturally avoided — the memo and
    its candle list match by identity.
    """
    registry = ctx.bars_registry
    if registry is None:
        return None
    view = registry.get_view(ctx.symbol, interval)
    if view is None:
        return None
    candles = view.memo.candles
    sub_ctx = EvaluationContext(
        symbol=ctx.symbol,
        interval=interval,
        bars=view.bars,
        candles=candles,
        current_index=max(0, len(candles) - 1),
        memo=view.memo,
        bars_registry=registry,
    )
    return sub_ctx


def evaluate_field(ref: FieldRef, ctx: EvaluationContext) -> float | None:
    """Resolve ``ref`` at the context's current index."""
    return evaluate_field_at(ref, ctx, ctx.current_index)


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------


def evaluate_condition(cond: Condition, ctx: EvaluationContext) -> bool | None:
    """Evaluate a single Condition. Returns True / False / None.

    Disabled conditions return ``None``; the parent group's filter
    skips them entirely, so a disabled True child doesn't degrade the
    group result.

    Cross-interval semantics: if ``cond.interval`` is non-empty and
    differs from ``ctx.interval``, the condition is evaluated against
    a sibling context built from ``ctx.bars_registry`` for
    ``(symbol, cond.interval)``. Without a registry, the historical
    silent-``None`` gate is preserved (back-compat).

    Within-last-N-bars: if ``cond.within_last_bars > 0``, the predicate
    is evaluated across the look-back window per ``cond.within_last_mode``
    (``"any"`` / ``"all"`` / ``"exactly"``), with daily-reset session
    clamping and forming-bar skip for transitions. See
    :func:`_walk_lookback_condition` for the full semantics. The
    cross-interval redirect happens BEFORE the look-back walk so the
    walk runs in the condition's own interval space.
    """
    if not cond.enabled:
        return None
    if cond.interval and cond.interval != ctx.interval:
        # Cross-interval condition: redirect to a sub-context built
        # against the registry's bars/memo for the alternate interval.
        # Preserve within_last_* on the local copy so the look-back
        # walk happens in the cond's own interval space (per spec).
        if ctx.bars_registry is None:
            return None
        sub_ctx = _sub_context_for_interval(ctx, cond.interval)
        if sub_ctx is None:
            return None
        local_cond = Condition(
            id=cond.id,
            left=cond.left,
            op=cond.op,
            params=dict(cond.params),
            interval="",
            enabled=cond.enabled,
            within_last_bars=cond.within_last_bars,
            within_last_mode=cond.within_last_mode,
        )
        return evaluate_condition(local_cond, sub_ctx)

    if cond.within_last_bars > 0:
        return _walk_lookback_condition(cond, ctx)
    return _evaluate_condition_at(cond, ctx, ctx.current_index)


def _evaluate_condition_at(
    cond: Condition,
    ctx: EvaluationContext,
    index: int,
    *,
    _in_lookback_walk: bool = False,
) -> bool | None:
    """Operator-dispatch body for :func:`evaluate_condition`, parametrized by index.

    Same semantics as the public ``evaluate_condition`` for the
    same-interval, non-look-back path — with two extensions:

    * Every operator evaluates at bar ``index`` instead of
      ``ctx.current_index``. Out-of-range / not-enough-history paths
      return ``None`` per the existing tri-valued contract.

    * When ``_in_lookback_walk`` is True AND the bar at ``index`` is
      the forming bar (i.e. ``index == ctx.current_index`` and
      ``ctx.is_forming``) AND the operator is a transition
      (``crosses_above`` / ``crosses_below``), this returns ``None``
      — the trader-spec'd "transitions on closed bars only" rule.
      Comparison operators stay live regardless.

    This helper is internal; callers should use ``evaluate_condition``
    (which handles disabled / cross-interval / within-last gating)
    unless they specifically need to evaluate at a non-current index
    inside a look-back walk.
    """
    op = cond.op
    p = cond.params
    i = index
    bars = ctx.bars

    # Forming-bar skip for transitions, only when within a look-back
    # walk (the comparisons-stay-live invariant). Outside a walk this
    # is a no-op so today's behavior is preserved.
    if (
        _in_lookback_walk
        and op in _TRANSITION_OPS
        and i == ctx.current_index
        and ctx.is_forming
    ):
        return None

    # --- 6 binary comparisons -----------------------------------------------
    if op in (OP_GT, OP_LT, OP_GE, OP_LE, OP_EQ, OP_NE):
        l = evaluate_field_at(cond.left, ctx, i)
        r = evaluate_field_at(p["right"], ctx, i)
        if l is None or r is None:
            return None
        if op == OP_GT: return l >  r
        if op == OP_LT: return l <  r
        if op == OP_GE: return l >= r
        if op == OP_LE: return l <= r
        if op == OP_EQ: return l == r
        return l != r  # OP_NE

    # --- between ------------------------------------------------------------
    if op == OP_BETWEEN:
        l  = evaluate_field_at(cond.left, ctx, i)
        lo = evaluate_field_at(p["low"], ctx, i)
        hi = evaluate_field_at(p["high"], ctx, i)
        if l is None or lo is None or hi is None:
            return None
        return lo <= l <= hi

    # --- crosses ------------------------------------------------------------
    if op in (OP_CROSSES_ABOVE, OP_CROSSES_BELOW):
        lookback = int(p["lookback"])
        if lookback < 1 or i - lookback < 0:
            return None
        prev_l = evaluate_field_at(cond.left, ctx, i - lookback)
        prev_r = evaluate_field_at(p["right"], ctx, i - lookback)
        cur_l  = evaluate_field_at(cond.left, ctx, i)
        cur_r  = evaluate_field_at(p["right"], ctx, i)
        if None in (prev_l, prev_r, cur_l, cur_r):
            return None
        if op == OP_CROSSES_ABOVE:
            return prev_l <= prev_r and cur_l > cur_r
        return prev_l >= prev_r and cur_l < cur_r

    # --- monotonic ----------------------------------------------------------
    if op in (OP_IS_RISING, OP_IS_FALLING):
        lookback = int(p["lookback"])
        if lookback < 1 or i - lookback < 0:
            return None
        vals: list[float | None] = [
            evaluate_field_at(cond.left, ctx, j) for j in range(i - lookback, i + 1)
        ]
        if any(v is None for v in vals):
            return None
        if op == OP_IS_RISING:
            return all(vals[k] < vals[k + 1] for k in range(len(vals) - 1))
        return all(vals[k] > vals[k + 1] for k in range(len(vals) - 1))

    # --- within_pct ---------------------------------------------------------
    if op == OP_WITHIN_PCT:
        l = evaluate_field_at(cond.left, ctx, i)
        t = evaluate_field_at(p["target"], ctx, i)
        if l is None or t is None:
            return None
        if t == 0.0:
            return None  # zero-target → undefined per spec.
        try:
            tol = float(p["tolerance_pct"])
        except (TypeError, ValueError):
            return None
        return abs((l - t) / t) * 100.0 <= tol

    # --- new high / low over n bars -----------------------------------------
    if op in (OP_NEW_HIGH_N, OP_NEW_LOW_N):
        n = int(p["n"])
        if n < 1 or i - n < 0:
            return None
        cur = evaluate_field_at(cond.left, ctx, i)
        if cur is None:
            return None
        prior_vals: list[float | None] = [
            evaluate_field_at(cond.left, ctx, j) for j in range(i - n, i)
        ]
        if any(v is None for v in prior_vals):
            return None
        if op == OP_NEW_HIGH_N:
            return cur > max(prior_vals)
        return cur < min(prior_vals)

    # --- holding above / below ----------------------------------------------
    if op in (OP_HOLDING_ABOVE, OP_HOLDING_BELOW):
        bars_n = int(p["bars"])
        if bars_n < 1 or i - bars_n + 1 < 0:
            return None
        for j in range(i - bars_n + 1, i + 1):
            l = evaluate_field_at(cond.left, ctx, j)
            r = evaluate_field_at(p["reference"], ctx, j)
            if l is None or r is None:
                return None
            if op == OP_HOLDING_ABOVE and not (l > r):
                return False
            if op == OP_HOLDING_BELOW and not (l < r):
                return False
        return True

    # --- structural: inside / outside / NR7 ---------------------------------
    if op in (OP_INSIDE_BAR, OP_OUTSIDE_BAR):
        if i < 1:
            return None
        h_now, h_prev = bars.high[i], bars.high[i - 1]
        l_now, l_prev = bars.low[i],  bars.low[i - 1]
        if any(_is_nan_like(x) for x in (h_now, h_prev, l_now, l_prev)):
            return None
        if op == OP_INSIDE_BAR:
            return bool(h_now < h_prev and l_now > l_prev)
        return bool(h_now > h_prev and l_now < l_prev)

    if op == OP_NR7:
        if i < 6:
            return None
        ranges = bars.high[i - 6 : i + 1] - bars.low[i - 6 : i + 1]
        if np.any(np.isnan(ranges)):
            return None
        cur_range = float(ranges[-1])
        prior_min = float(ranges[:-1].min())
        return bool(cur_range <= prior_min)

    LOG.error("_evaluate_condition_at: unknown op %r on cond %s", op, cond.id)
    return None


# ---------------------------------------------------------------------------
# Group + Scan evaluation
# ---------------------------------------------------------------------------


def evaluate_group(grp: Group, ctx: EvaluationContext) -> bool | None:
    """Tri-valued AND/OR over enabled children. Empty group → None.

    When ``grp.within_last_bars > 0``, the entire subtree is re-evaluated
    at each bar in the look-back window per ``grp.within_last_mode``;
    a Group-level look-back lets users express "(EMA cross AND volume
    spike) on the SAME bar, anywhere in the last N bars" — strictly
    more expressive than per-Condition look-back, which only requires
    each leaf to fire SOMEWHERE in its own window (possibly on
    different bars). See :func:`_walk_lookback_group`.
    """
    if not grp.enabled:
        return None
    if grp.within_last_bars > 0:
        return _walk_lookback_group(grp, ctx)
    return _evaluate_group_at(grp, ctx, ctx.current_index)


def _evaluate_group_at(
    grp: Group,
    ctx: EvaluationContext,
    index: int,
    *,
    _in_lookback_walk: bool = False,
) -> bool | None:
    """Body of :func:`evaluate_group`, parametrized by bar index.

    Recurses into children with the same ``index`` so a Group-level
    look-back walk evaluates the entire subtree at each bar in the
    window. The ``_in_lookback_walk`` flag propagates into leaves so
    the forming-bar transition skip applies inside Group-level walks
    too.

    Children that carry their own ``within_last_bars`` retain their
    semantics: when called at a non-current index inside a Group walk,
    the child's own look-back window is anchored to ``index`` (its
    "current" for the purpose of this evaluation), so per-Condition
    look-back composes naturally with per-Group look-back.
    """
    results: list[bool | None] = []
    for child in grp.children:
        if not getattr(child, "enabled", True):
            continue  # skip disabled — not the same as None
        if isinstance(child, Condition):
            r = _evaluate_child_condition_at(
                child, ctx, index, _in_lookback_walk=_in_lookback_walk
            )
        elif isinstance(child, Group):
            r = _evaluate_child_group_at(
                child, ctx, index, _in_lookback_walk=_in_lookback_walk
            )
        else:
            LOG.error("_evaluate_group_at: unknown child type %r", type(child).__name__)
            continue
        results.append(r)
    if not results:
        return None
    if grp.combinator == "and":
        if any(r is False for r in results):
            return False
        if all(r is True for r in results):
            return True
        return None
    # combinator == "or"
    if any(r is True for r in results):
        return True
    if all(r is False for r in results):
        return False
    return None


def _evaluate_child_condition_at(
    cond: Condition,
    ctx: EvaluationContext,
    index: int,
    *,
    _in_lookback_walk: bool,
) -> bool | None:
    """Evaluate a Condition child of a Group at ``index``.

    Honors the child's own disabled / cross-interval / within-last
    gates, but with the parent's ``index`` taken as the "current" for
    nested look-back semantics. Cross-interval children inside a
    parent Group walk fall back to the existing single-bar redirect
    (sub-context's ``current_index``) — the per-Group walk's
    cross-interval index mapping is a v2 refinement (documented as a
    known limitation in the spec).
    """
    if not cond.enabled:
        return None
    if cond.interval and cond.interval != ctx.interval:
        # Cross-interval child of a Group walk: defer to the public
        # wrapper, which redirects to the sub-context. This evaluates
        # the child at the sub-context's current_index regardless of
        # the parent walk's ``index`` — a known v1 imprecision.
        return evaluate_condition(cond, ctx)
    if cond.within_last_bars > 0:
        # Per-Condition look-back composes with the parent walk: the
        # child's window is anchored to the parent walk's ``index``,
        # not ``ctx.current_index``. Build a temporary "view" context
        # by passing index as the anchor through a helper.
        return _walk_lookback_condition(cond, ctx, anchor_index=index)
    return _evaluate_condition_at(
        cond, ctx, index, _in_lookback_walk=_in_lookback_walk
    )


def _evaluate_child_group_at(
    grp: Group,
    ctx: EvaluationContext,
    index: int,
    *,
    _in_lookback_walk: bool,
) -> bool | None:
    """Evaluate a Group child of a parent Group walk at ``index``."""
    if not grp.enabled:
        return None
    if grp.within_last_bars > 0:
        return _walk_lookback_group(grp, ctx, anchor_index=index)
    return _evaluate_group_at(
        grp, ctx, index, _in_lookback_walk=_in_lookback_walk
    )


# ---------------------------------------------------------------------------
# Within-last-N-bars walk
# ---------------------------------------------------------------------------


def _bar_timestamp_iso(bars: BarsNp, index: int) -> str:
    """Best-effort ISO-8601 timestamp string for ``bars.timestamps[index]``.

    Returns an empty string if ``index`` is out of range or the
    underlying array slot is unset.
    """
    if index < 0 or index >= bars.timestamps.size:
        return ""
    ts = bars.timestamps[index]
    try:
        # ``datetime64[ns]`` → str like "2026-05-06T13:35:00.000000000"
        return str(np.datetime_as_string(ts, unit="s"))
    except (ValueError, TypeError):
        return ""


def _record_condition_evidence(
    ctx: EvaluationContext,
    cond: Condition,
    trigger_index: int,
    bars_ago: int,
) -> None:
    """Append a :class:`MatchEvidence` for a triggered Condition look-back."""
    value = evaluate_field_at(cond.left, ctx, trigger_index)
    ctx.evidence.append(
        MatchEvidence(
            node_id=cond.id,
            bars_ago=int(bars_ago),
            timestamp=_bar_timestamp_iso(ctx.bars, trigger_index),
            value=value,
        )
    )


def _record_group_evidence(
    ctx: EvaluationContext,
    grp: Group,
    trigger_index: int,
    bars_ago: int,
) -> None:
    """Append a :class:`MatchEvidence` for a triggered Group look-back."""
    ctx.evidence.append(
        MatchEvidence(
            node_id=grp.id,
            bars_ago=int(bars_ago),
            timestamp=_bar_timestamp_iso(ctx.bars, trigger_index),
            value=None,
        )
    )


def _walk_lookback_condition(
    cond: Condition,
    ctx: EvaluationContext,
    *,
    anchor_index: int | None = None,
) -> bool | None:
    """Walk the look-back window for a Condition. See module docstring.

    ``anchor_index`` defaults to ``ctx.current_index``; pass an
    explicit value when called from within a parent Group walk so the
    child's window is anchored to the parent walk's index.
    """
    n = int(cond.within_last_bars)
    mode = cond.within_last_mode
    hi = ctx.current_index if anchor_index is None else int(anchor_index)
    lo = max(0, hi - n)
    # Daily-reset clamp.
    if condition_uses_daily_reset_field(cond):
        session_lo = find_session_open_index(ctx.bars, hi)
        lo = max(lo, session_lo)

    def eval_at(j: int) -> bool | None:
        return _evaluate_condition_at(cond, ctx, j, _in_lookback_walk=True)

    if mode == WITHIN_LAST_MODE_EXACTLY:
        target = hi - n
        if target < lo or target < 0 or target > hi:
            return None
        result = eval_at(target)
        if result is True:
            _record_condition_evidence(ctx, cond, target, n)
        return result

    if mode == WITHIN_LAST_MODE_ALL:
        saw_none = False
        for j in range(lo, hi + 1):
            r = eval_at(j)
            if r is False:
                return False
            if r is None:
                saw_none = True
        if saw_none:
            return None
        # All bars in window were True — record evidence at the oldest
        # bar (start of the run). bars_ago equals the window depth.
        _record_condition_evidence(ctx, cond, lo, hi - lo)
        return True

    # WITHIN_LAST_MODE_ANY (default). Walk most-recent first to
    # short-circuit the bread-and-butter case in O(1).
    saw_none = False
    for j in range(hi, lo - 1, -1):
        r = eval_at(j)
        if r is True:
            _record_condition_evidence(ctx, cond, j, hi - j)
            return True
        if r is None:
            saw_none = True
    if saw_none:
        return None
    return False


def _walk_lookback_group(
    grp: Group,
    ctx: EvaluationContext,
    *,
    anchor_index: int | None = None,
) -> bool | None:
    """Walk the look-back window for a Group. See module docstring.

    Same shape as :func:`_walk_lookback_condition` but with
    :func:`_evaluate_group_at` as the inner evaluator and group-level
    evidence recording (no LHS scalar value).
    """
    n = int(grp.within_last_bars)
    mode = grp.within_last_mode
    hi = ctx.current_index if anchor_index is None else int(anchor_index)
    lo = max(0, hi - n)
    if condition_uses_daily_reset_field(grp):
        session_lo = find_session_open_index(ctx.bars, hi)
        lo = max(lo, session_lo)

    def eval_at(j: int) -> bool | None:
        return _evaluate_group_at(grp, ctx, j, _in_lookback_walk=True)

    if mode == WITHIN_LAST_MODE_EXACTLY:
        target = hi - n
        if target < lo or target < 0 or target > hi:
            return None
        result = eval_at(target)
        if result is True:
            _record_group_evidence(ctx, grp, target, n)
        return result

    if mode == WITHIN_LAST_MODE_ALL:
        saw_none = False
        for j in range(lo, hi + 1):
            r = eval_at(j)
            if r is False:
                return False
            if r is None:
                saw_none = True
        if saw_none:
            return None
        _record_group_evidence(ctx, grp, lo, hi - lo)
        return True

    saw_none = False
    for j in range(hi, lo - 1, -1):
        r = eval_at(j)
        if r is True:
            _record_group_evidence(ctx, grp, j, hi - j)
            return True
        if r is None:
            saw_none = True
    if saw_none:
        return None
    return False


def evaluate_scan(scan: ScanDefinition, ctx: EvaluationContext) -> bool | None:
    """Evaluate a full scan against one symbol/interval context."""
    return evaluate_group(scan.root, ctx)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_scan(
    scan: ScanDefinition,
    *,
    bars_registry: Any | None = None,
) -> list[str]:
    """Return a list of human-readable validation errors. Empty = OK.

    Walks every Condition in the tree and validates its left field plus
    every FieldRef in its params dict against the scanner field
    registry. Also validates ``rank_by`` if set.

    Cross-interval rule: when ``bars_registry`` is ``None`` (the v1
    back-compat path), any :class:`Condition` or :class:`FieldRef`
    whose ``interval`` is non-empty and differs from
    ``scan.primary_interval`` is rejected — the engine has no source
    for the alternate-interval bars in that mode. When a registry is
    provided the check is skipped: the engine resolves the sibling
    interval via the registry on demand.

    Pure: doesn't touch any candles or memo.
    """
    errs: list[str] = []
    primary = scan.primary_interval
    for cond in scan.all_conditions():
        try:
            validate_field_ref(cond.left)
        except ValueError as e:
            errs.append(f"condition {cond.id} ({cond.op}) left: {e}")
        for k, v in cond.params.items():
            if isinstance(v, FieldRef):
                try:
                    validate_field_ref(v)
                except ValueError as e:
                    errs.append(f"condition {cond.id} ({cond.op}) param {k!r}: {e}")
        if bars_registry is None:
            if cond.interval and cond.interval != primary:
                errs.append(
                    f"condition {cond.id} ({cond.op}) interval "
                    f"{cond.interval!r} differs from scan primary_interval "
                    f"{primary!r} but no BarsRegistry was provided; "
                    f"mixed-interval scans require a registry"
                )
            for k, v in cond.params.items():
                if isinstance(v, FieldRef) and v.interval and v.interval != primary:
                    errs.append(
                        f"condition {cond.id} ({cond.op}) param {k!r} interval "
                        f"{v.interval!r} differs from scan primary_interval "
                        f"{primary!r} but no BarsRegistry was provided; "
                        f"mixed-interval scans require a registry"
                    )
            if cond.left.interval and cond.left.interval != primary:
                errs.append(
                    f"condition {cond.id} ({cond.op}) left interval "
                    f"{cond.left.interval!r} differs from scan primary_interval "
                    f"{primary!r} but no BarsRegistry was provided; "
                    f"mixed-interval scans require a registry"
                )
    if scan.rank_by is not None:
        try:
            validate_field_ref(scan.rank_by)
        except ValueError as e:
            errs.append(f"rank_by: {e}")
        if (
            bars_registry is None
            and scan.rank_by.interval
            and scan.rank_by.interval != primary
        ):
            errs.append(
                f"rank_by interval {scan.rank_by.interval!r} differs from "
                f"scan primary_interval {primary!r} but no BarsRegistry was "
                f"provided; mixed-interval scans require a registry"
            )
    return errs


__all__ = [
    "IndicatorMemo",
    "EvaluationContext",
    "make_context",
    "evaluate_field",
    "evaluate_field_at",
    "evaluate_condition",
    "evaluate_group",
    "evaluate_scan",
    "validate_scan",
]
