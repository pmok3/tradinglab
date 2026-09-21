"""Vectorized strategy-evaluation plan for the mechanical strategy tester.

The legacy per-bar loop in :mod:`strategy_tester.evaluator` spends most of
its wall-clock in per-bar Python dispatch: building trigger contexts,
calling the shared entry/exit registries, constructing spec ``Bar``
adapters, and (for the arm-window gate) building datetimes and re-parsing
``"HH:MM"`` strings. This module precomputes every *decision fact* that
does not depend on path-dependent engine state as NumPy boolean masks /
scalar thresholds **once per symbol**, so the hot loop degrades to a
handful of array lookups plus the genuinely path-dependent bookkeeping
(engine fills, fire counters, cooldown, edge-trigger state).

Bit-identity contract
---------------------
Every mask replicates the scalar predicate it replaces *exactly*,
including NaN semantics (Python ``float`` comparisons — NaN never
compares true), first-bar behaviour, inclusive ``<=`` / ``>=``
boundaries, midnight-wrapping arm windows, and the unknown-as-not-matched
mapping for scanner tri-state results. Anything that cannot be proven
identical falls back to the legacy per-bar path: :func:`build_plan`
returns ``None`` and the caller runs the untouched scalar loop.

What is (and is not) vectorized
-------------------------------
* Entry price triggers (MARKET/LIMIT/STOP/STOP_LIMIT): one boolean mask
  each over the whole OHLC arrays.
* Entry INDICATOR / SCANNER_ALERT: all-bars masks from
  :func:`scanner.engine.evaluate_group_vec` (``None`` → legacy fallback
  for the whole symbol).
* Entry time gates: arm-window and require-market-open become boolean
  masks derived from the precomputed ET arrays — no per-bar datetimes.
* Exit MARKET / TIME_OF_DAY / INDICATOR: global boolean masks.
* Exit LIMIT / STOP / STOP_LIMIT: the price *target* is resolved from the
  position's average entry price (constant per holding period while the
  entry policy is BLOCK); the per-bar touch check is a scalar compare.
* Exit TRAILING_STOP / CHANDELIER: these recurrences are inherently
  sequential, so the vectorized loop advances them with the *real*
  :mod:`exits.spec` update/evaluate functions (bit-identical by
  construction) instead of the full dispatch path — no per-bar context
  building, adapter allocation, or datetime construction.

Fallback surface (documented, not silent)
-----------------------------------------
:func:`build_plan` returns ``None`` — i.e. the symbol runs the legacy
loop unchanged — when:

* an entry/exit trigger kind is missing from the shared dispatch
  registry (preserves the typed ``UnsupportedTriggerKind`` contract),
* any INDICATOR / SCANNER_ALERT condition tree is outside
  :func:`evaluate_group_vec`'s supported subset (within-last
  quantifiers, cross-symbol / cross-interval refs, unsupported
  operators — anything the vec evaluator reports as ``None``),
* the entry strategy's ``position_already_open_policy`` is not BLOCK
  (mid-holding adds would move the average entry price that exit
  targets and stateful-exit position views are resolved from).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any

import numpy as np

from ..constants import is_intraday
from ..core.side import Side
from ..entries.dispatch import _ENTRY_DISPATCH
from ..entries.model import Direction as EntryDirection
from ..entries.model import PositionAlreadyOpenPolicy
from ..entries.model import TriggerKind as EntryTriggerKind
from ..exits.dispatch import _EXIT_DISPATCH
from ..exits.model import TriggerKind as ExitTriggerKind
from ..exits.spec import Bar as _SpecBar
from ..exits.spec import TriggerState as _SpecTriggerState
from ..exits.spec import evaluate_chandelier_stop as _eval_chandelier_stop
from ..exits.spec import evaluate_trailing_stop as _eval_trailing_stop
from ..exits.spec import update_chandelier_state as _update_chandelier_state
from ..exits.spec import update_trail_state as _update_trail_state
from ..positions.model import Position as _Position
from ..scanner.engine import evaluate_group_vec

LOG = logging.getLogger(__name__)

_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class VectorEvalPlan:
    """Precomputed decision facts for one symbol's vectorized evaluation.

    ``entry_fire`` / ``entry_static_gate`` are ``bool[n]`` masks; the hot
    loop combines them with the path-dependent gates (fire caps,
    cooldown, BLOCK policy, scanner-alert edge state).

    ``exit_legs`` are the enabled exit triggers in evaluation order;
    ``exit_static_masks`` maps leg index → ``bool[n]`` for the
    side-independent legs (MARKET / TIME_OF_DAY / INDICATOR). Price legs
    resolve their target lazily into ``exit_targets`` on first check;
    stateful legs keep their :class:`exits.spec.TriggerState` in the
    evaluator's ``ctx.trigger_states`` exactly like the legacy path.

    ``position_view`` / ``exit_targets`` / ``bar_views`` are per-holding
    runtime, rebuilt by :func:`activate_holding` at each position-open
    transition.
    """

    n: int
    # -- entry --
    entry_kind: EntryTriggerKind
    entry_is_long: bool
    entry_trigger_id: Any
    entry_fire: np.ndarray
    entry_is_scanner_alert: bool
    entry_static_gate: np.ndarray
    # -- exits --
    exits_vectorized: bool
    exit_legs: list[Any] = field(default_factory=list)
    exit_static_masks: dict[int, np.ndarray] = field(default_factory=dict)
    # -- per-holding runtime --
    position_view: Any = None
    exit_targets: dict[str, float | None] = field(default_factory=dict)
    bar_views: dict[str, _SpecBar] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small parsers (mirrors of evaluator.py helpers — kept local so this
# module never imports the evaluator and creates a cycle)
# ---------------------------------------------------------------------------


def _parse_hhmm(s: str | None) -> time | None:
    """Parse ``"HH:MM"`` → :class:`datetime.time`; ``None`` when blank/malformed.

    Mirrors ``evaluator._parse_hhmm_to_time`` semantics exactly.
    """
    if not s:
        return None
    try:
        h, m = s.split(":")
        return time(hour=int(h), minute=int(m))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Entry masks
# ---------------------------------------------------------------------------


def _entry_static_gate(
    entry_strategy: Any,
    interval: str,
    et_tod_sec: np.ndarray,
    rth_mask: np.ndarray,
    n: int,
) -> np.ndarray:
    """Boolean mask for the *static* entry gates (enabled, arm-window, RTH).

    Replicates :func:`evaluator._check_entry`'s gate order outcome:
    ``enabled`` → intraday-only arm-window → ``require_market_open``.
    The dynamic gates (fire caps, cooldown, BLOCK policy) stay scalar in
    the hot loop because they depend on path-dependent counters.
    """
    if not entry_strategy.enabled:
        return np.zeros(n, dtype=bool)
    gate = np.ones(n, dtype=bool)
    intraday = is_intraday(interval) if interval else True
    if intraday:
        start = _parse_hhmm(entry_strategy.arm_window_start)
        end = _parse_hhmm(entry_strategy.arm_window_end)
        if start is not None and end is not None:
            # ``_within_arm_window`` compares ``et_dt.time()`` against the
            # HH:MM bounds; bar timestamps are whole seconds so comparing
            # seconds-since-ET-midnight is exact (inclusive both ends,
            # midnight wrap supported).
            s = start.hour * 3600 + start.minute * 60
            e = end.hour * 3600 + end.minute * 60
            if s <= e:
                gate &= (et_tod_sec >= s) & (et_tod_sec <= e)
            else:
                gate &= (et_tod_sec >= s) | (et_tod_sec <= e)
        if entry_strategy.require_market_open:
            gate &= rth_mask
    return gate


def _vec_condition(
    trigger: Any,
    kind: Any,
    eval_ctx: Any,
    normalized_conditions: dict | None,
) -> Any | None:
    """Condition tree for vectorized scanner evaluation, or ``None``.

    ``None`` means "the scalar handler would silently no-fire here"
    (missing context / missing condition) — the caller emits an
    all-False mask. A tree *present* but outside the vec subset is
    reported via :func:`evaluate_group_vec` returning ``None``.
    """
    if kind is EntryTriggerKind.SCANNER_ALERT:
        # Mirrors entries.dispatch._h_scanner_alert's silent no-fire set.
        if eval_ctx is None or not trigger.scanner_id:
            return None
        if normalized_conditions is None:
            return None
        return normalized_conditions.get(trigger.id)
    # INDICATOR (entry or exit) — mirrors _h_indicator's guards.
    if eval_ctx is None or trigger.condition is None:
        return None
    condition = trigger.condition
    if normalized_conditions is not None:
        condition = normalized_conditions.get(trigger.id, condition)
    return condition


def _scanner_fire_mask(
    trigger: Any,
    kind: Any,
    eval_ctx: Any,
    normalized_conditions: dict | None,
    n: int,
) -> np.ndarray | None:
    """All-bars ``is_true`` mask for an INDICATOR / SCANNER_ALERT trigger.

    Returns ``None`` when the tree is outside
    :func:`evaluate_group_vec`'s supported subset — the caller must fall
    back to the legacy per-bar path for the whole symbol.
    """
    condition = _vec_condition(trigger, kind, eval_ctx, normalized_conditions)
    if condition is None:
        return np.zeros(n, dtype=bool)
    masks = evaluate_group_vec(condition, eval_ctx)
    if masks is None:
        return None
    return np.asarray(masks[0], dtype=bool)


def _entry_fire_mask(
    *,
    trigger: Any,
    kind: EntryTriggerKind,
    is_long: bool,
    bars: Any,
    n: int,
    eval_ctx: Any,
    normalized_conditions: dict | None,
) -> np.ndarray | None:
    """Boolean fire mask for the entry trigger.

    Replicates ``entries.spec.should_fire_*`` / the INDICATOR +
    SCANNER_ALERT dispatch handlers exactly, including NaN semantics
    (NaN never compares true, matching Python ``float`` behaviour).
    """
    low = bars.low
    high = bars.high
    if kind is EntryTriggerKind.MARKET:
        return np.ones(n, dtype=bool)
    if kind is EntryTriggerKind.LIMIT:
        if trigger.price is None:
            return np.zeros(n, dtype=bool)
        px = float(trigger.price)
        return (low <= px) if is_long else (high >= px)
    if kind is EntryTriggerKind.STOP:
        if trigger.stop_price is None:
            return np.zeros(n, dtype=bool)
        px = float(trigger.stop_price)
        return (high >= px) if is_long else (low <= px)
    if kind is EntryTriggerKind.STOP_LIMIT:
        if trigger.stop_price is None or trigger.price is None:
            return np.zeros(n, dtype=bool)
        sp = float(trigger.stop_price)
        px = float(trigger.price)
        if is_long:
            return (high >= sp) & (low <= px)
        return (low <= sp) & (high >= px)
    # INDICATOR / SCANNER_ALERT.
    return _scanner_fire_mask(
        trigger, kind, eval_ctx, normalized_conditions, n,
    )


# ---------------------------------------------------------------------------
# Exit masks / helpers
# ---------------------------------------------------------------------------


def _tod_mask(trigger: Any, et_tod_sec: np.ndarray, n: int) -> np.ndarray:
    """Boolean mask for a TIME_OF_DAY exit leg.

    Replicates ``exits.spec.evaluate_time_of_day``'s
    ``now.time() >= cutoff`` comparison (level-triggered; the evaluator
    de-dupes via position state). Malformed cutoffs → all-False,
    mirroring the scalar silent no-fire.
    """
    tod = trigger.time_of_day
    if not tod:
        return np.zeros(n, dtype=bool)
    try:
        h, m = tod.split(":")
        cutoff = int(h) * 3600 + int(m) * 60
    except (ValueError, AttributeError):
        return np.zeros(n, dtype=bool)
    return et_tod_sec >= cutoff


def _resolve_exit_target(trigger: Any, position: Any) -> float | None:
    """Resolve a LIMIT / STOP / STOP_LIMIT exit price target.

    Line-for-line mirror of ``exits.dispatch._legacy_resolve_exit_price``
    (the strategy tester runs with ``legacy_signed_offsets=True``).
    Resolved lazily at the first bar the leg is checked so malformed
    configs raise with the same timing as the legacy per-bar path.
    """
    if trigger.price is not None:
        return float(trigger.price)
    ref = position.avg_entry_price
    leg_sign = 1.0 if trigger.kind is ExitTriggerKind.LIMIT else -1.0
    side_sign = 1.0 if position.side == "long" else -1.0
    offset_pct = trigger.offset_pct
    offset_dollars = trigger.offset_dollar
    if offset_pct is not None:
        return ref * (1.0 + side_sign * leg_sign * float(offset_pct) / 100.0)
    if offset_dollars is not None:
        return ref + side_sign * leg_sign * float(offset_dollars)
    return None


def _make_position_view(ctx: Any) -> Any:
    """Build a :class:`positions.model.Position` for the vectorized exit path.

    Mirrors ``evaluator._ctx_to_position`` except the ``entry_time``
    datetime (never read by the exit evaluators) is a constant, so no
    per-bar ``datetime.fromtimestamp`` happens on the hot path. The
    caller refreshes ``qty_open`` / ``qty_initial`` from the synced
    context once per bar; side / average price are constant per holding
    period while the entry policy is BLOCK.
    """
    side = Side.from_str(ctx.position_side).as_long_short()
    return _Position(
        id=f"strat-test-pos-{ctx.symbol}",
        symbol=ctx.symbol,
        side=side,  # type: ignore[arg-type]
        qty_initial=float(ctx.position_qty),
        qty_open=float(ctx.position_qty),
        avg_entry_price=float(ctx.position_avg_price),
        entry_time=_EPOCH_UTC,
        source="sandbox",  # type: ignore[arg-type]
    )


def _bar_view(plan: VectorEvalPlan, tid: str, o: float, h: float, l: float, c: float) -> _SpecBar:
    """Reusable dateless spec-Bar for a stateful exit leg.

    The trailing-stop / chandelier evaluators only read OHLC; the
    legacy adapter's per-bar UTC datetime is skipped.
    """
    bv = plan.bar_views.get(tid)
    if bv is None:
        bv = _SpecBar(open=0.0, high=0.0, low=0.0, close=0.0)
        plan.bar_views[tid] = bv
    bv.open = o
    bv.high = h
    bv.low = l
    bv.close = c
    return bv


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


def build_plan(
    *,
    n: int,
    bars: Any,
    interval: str,
    entry_strategy: Any,
    exit_strategy: Any,
    et_tod_sec: np.ndarray,
    rth_mask: np.ndarray,
    eval_ctx: Any,
    normalized_conditions: dict | None,
) -> VectorEvalPlan | None:
    """Build the vectorized evaluation plan, or ``None`` for legacy fallback.

    ``None`` is returned whenever exact vectorization cannot be proven
    (see the module docstring's fallback surface) — the caller then runs
    the untouched scalar loop.
    """
    entry_trigger = entry_strategy.trigger
    entry_kind = entry_trigger.kind
    if entry_kind not in _ENTRY_DISPATCH:
        # Preserves the typed UnsupportedTriggerKind contract: the
        # legacy path raises it before dispatch.
        return None
    is_long = entry_strategy.direction is EntryDirection.LONG
    entry_fire = _entry_fire_mask(
        trigger=entry_trigger,
        kind=entry_kind,
        is_long=is_long,
        bars=bars,
        n=n,
        eval_ctx=eval_ctx,
        normalized_conditions=normalized_conditions,
    )
    if entry_fire is None:
        return None
    entry_static_gate = _entry_static_gate(
        entry_strategy, interval, et_tod_sec, rth_mask, n,
    )

    exit_legs: list[Any] = []
    exit_static_masks: dict[int, np.ndarray] = {}
    for leg in exit_strategy.legs:
        if not leg.enabled:
            continue
        for trigger in leg.triggers:
            if not trigger.enabled:
                continue
            if trigger.kind not in _EXIT_DISPATCH:
                return None
            idx = len(exit_legs)
            exit_legs.append(trigger)
            kind = trigger.kind
            if kind is ExitTriggerKind.MARKET:
                exit_static_masks[idx] = np.ones(n, dtype=bool)
            elif kind is ExitTriggerKind.TIME_OF_DAY:
                exit_static_masks[idx] = _tod_mask(trigger, et_tod_sec, n)
            elif kind is ExitTriggerKind.INDICATOR:
                mask = _scanner_fire_mask(
                    trigger, kind, eval_ctx, normalized_conditions, n,
                )
                if mask is None:
                    return None
                exit_static_masks[idx] = mask
            # LIMIT / STOP / STOP_LIMIT resolve per-holding targets;
            # TRAILING_STOP / CHANDELIER advance exact recurrences.

    # Exit targets and stateful-exit position views are resolved from the
    # position's average entry price, which is only constant per holding
    # period when re-entry while open is blocked. Anything else keeps
    # the legacy per-bar exit path (the entry side stays vectorized).
    exits_vectorized = (
        entry_strategy.position_already_open_policy
        is PositionAlreadyOpenPolicy.BLOCK
    )
    return VectorEvalPlan(
        n=n,
        entry_kind=entry_kind,
        entry_is_long=is_long,
        entry_trigger_id=entry_trigger.id,
        entry_fire=np.ascontiguousarray(entry_fire, dtype=bool),
        entry_is_scanner_alert=entry_kind is EntryTriggerKind.SCANNER_ALERT,
        entry_static_gate=np.ascontiguousarray(entry_static_gate, dtype=bool),
        exits_vectorized=exits_vectorized,
        exit_legs=exit_legs,
        exit_static_masks=exit_static_masks,
    )


# ---------------------------------------------------------------------------
# Per-holding activation
# ---------------------------------------------------------------------------


def activate_holding(
    plan: VectorEvalPlan, ctx: Any, o: float, h: float, l: float, c: float,
) -> None:
    """(Re)build per-holding exit runtime at a position-open transition.

    Mirrors ``evaluator._reset_trigger_states_on_activation``: clears
    stateful exit state and seeds CHANDELIER states at the entry bar
    (``is_activation=True``). TRAILING_STOP states bootstrap lazily on
    first check, exactly like the legacy path.
    """
    ctx.trigger_states.clear()
    plan.exit_targets.clear()
    plan.position_view = _make_position_view(ctx)
    for trigger in plan.exit_legs:
        if trigger.kind is not ExitTriggerKind.CHANDELIER:
            continue
        state = _SpecTriggerState()
        try:
            _update_chandelier_state(
                state,
                trigger,
                plan.position_view,
                _bar_view(plan, trigger.id, o, h, l, c),
                is_activation=True,
            )
        except Exception:  # noqa: BLE001 — mirrors the legacy seed guard
            LOG.exception(
                "vector_eval: chandelier seed failed (trigger_id=%s)",
                trigger.id,
            )
            continue
        ctx.trigger_states[trigger.id] = state


# ---------------------------------------------------------------------------
# Vectorized exit check (mirrors evaluator._check_exits)
# ---------------------------------------------------------------------------


def _price_leg_fires(
    plan: VectorEvalPlan, trigger: Any, kind: Any, h: float, l: float,
) -> bool:
    """Fire check for LIMIT / STOP / STOP_LIMIT legs.

    Mirrors ``exits.dispatch._legacy_limit`` / ``_legacy_stop`` /
    ``_legacy_stop_limit``: target resolution (raising on malformed
    offsets exactly like the legacy per-bar path), then the legacy
    ``compute_qty_at_fire`` quantity gate (raising on malformed
    ``qty_pct``), then the touch comparison.
    """
    tid = trigger.id
    if tid not in plan.exit_targets:
        plan.exit_targets[tid] = _resolve_exit_target(trigger, plan.position_view)
    target = plan.exit_targets[tid]
    if target is None:
        return False
    qty = float(plan.position_view.qty_open) * float(trigger.qty_pct) / 100.0
    if qty <= 0:
        return False
    is_long = plan.position_view.side == "long"
    if kind is ExitTriggerKind.LIMIT:
        return (h >= target) if is_long else (l <= target)
    # STOP and STOP_LIMIT share the legacy stop-touch fire condition.
    return (l <= target) if is_long else (h >= target)


def _trailing_leg_fires(
    plan: VectorEvalPlan, ctx: Any, trigger: Any, o: float, h: float, l: float, c: float,
) -> bool:
    """Fire check for a TRAILING_STOP leg via the real spec evaluators."""
    tid = trigger.id
    state = ctx.trigger_states.get(tid)
    if state is None:
        state = _SpecTriggerState()
        ctx.trigger_states[tid] = state
    bv = _bar_view(plan, tid, o, h, l, c)
    try:
        _update_trail_state(state, trigger, plan.position_view, bv, is_close=True)
        decision = _eval_trailing_stop(state, trigger, plan.position_view, bv)
    except Exception:  # noqa: BLE001 — mirrors _h_trailing_stop's guard
        LOG.exception(
            "vector_eval: trailing_stop raised (trigger_id=%s)", tid,
        )
        return False
    return bool(decision.fire)


def _chandelier_leg_fires(
    plan: VectorEvalPlan, ctx: Any, trigger: Any, o: float, h: float, l: float, c: float,
) -> bool:
    """Fire check for a CHANDELIER leg via the real spec evaluators."""
    tid = trigger.id
    state = ctx.trigger_states.get(tid)
    if state is None:
        state = _SpecTriggerState()
        ctx.trigger_states[tid] = state
    bv = _bar_view(plan, tid, o, h, l, c)
    try:
        # Mirrors exits.dispatch._h_chandelier's activation detection.
        is_activation = state.chandelier_frozen_params is None
        _update_chandelier_state(
            state, trigger, plan.position_view, bv, is_activation=is_activation,
        )
        decision = _eval_chandelier_stop(state, trigger, plan.position_view, bv)
    except Exception:  # noqa: BLE001 — mirrors _h_chandelier's guard
        LOG.exception(
            "vector_eval: chandelier raised (trigger_id=%s)", tid,
        )
        return False
    return bool(decision.fire)


def _exit_leg_fires(
    plan: VectorEvalPlan,
    ctx: Any,
    idx: int,
    trigger: Any,
    i: int,
    o: float,
    h: float,
    l: float,
    c: float,
) -> bool:
    """Per-leg fire predicate, in evaluation order."""
    kind = trigger.kind
    mask = plan.exit_static_masks.get(idx)
    if mask is not None:
        if not bool(mask[i]):
            return False
        if kind is ExitTriggerKind.INDICATOR:
            # Mirrors _h_indicator: no quantity gate inside the handler.
            return True
        # MARKET / TIME_OF_DAY share the legacy compute_qty_at_fire gate.
        # TIME_OF_DAY swallows malformed qty_pct into no-fire (mirrors
        # _h_time_of_day); MARKET lets it raise (mirrors evaluate_market).
        try:
            qty = float(plan.position_view.qty_open) * float(trigger.qty_pct) / 100.0
        except (TypeError, ValueError):
            if kind is ExitTriggerKind.TIME_OF_DAY:
                return False
            raise
        return qty > 0
    if kind in (
        ExitTriggerKind.LIMIT,
        ExitTriggerKind.STOP,
        ExitTriggerKind.STOP_LIMIT,
    ):
        return _price_leg_fires(plan, trigger, kind, h, l)
    if kind is ExitTriggerKind.TRAILING_STOP:
        return _trailing_leg_fires(plan, ctx, trigger, o, h, l, c)
    if kind is ExitTriggerKind.CHANDELIER:
        return _chandelier_leg_fires(plan, ctx, trigger, o, h, l, c)
    return False  # unreachable: build_plan pins the kind set


def check_exits_vectorized(
    plan: VectorEvalPlan,
    ctx: Any,
    i: int,
    o: float,
    h: float,
    l: float,
    c: float,
) -> tuple[bool, float]:
    """Vectorized-path exit check; mirrors ``evaluator._check_exits``.

    First leg to fire wins. Returns ``(fired, qty_to_close)``.
    """
    if not ctx.position_open:
        return False, 0.0
    if ctx.position_qty <= 0.0:
        return False, 0.0
    pos = plan.position_view
    pos.qty_open = float(ctx.position_qty)
    pos.qty_initial = float(ctx.position_qty)
    for idx, trigger in enumerate(plan.exit_legs):
        if _exit_leg_fires(plan, ctx, idx, trigger, i, o, h, l, c):
            pct = max(0.0, min(100.0, float(trigger.qty_pct))) / 100.0
            qty_to_close = ctx.position_qty * pct
            if qty_to_close <= 0.0:
                continue
            return True, qty_to_close
    return False, 0.0
