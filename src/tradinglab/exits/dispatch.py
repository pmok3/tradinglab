"""Shared trigger-dispatch registry for exit strategies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import numpy as np

from ..backtest.bars import BarSeries
from ..core.side import Side
from ..positions.model import Position
from ..scanner.engine import evaluate_group as _evaluate_group
from ..scanner.engine import evaluate_group_vec
from .model import ExitTrigger, TriggerKind
from .spec import (
    Bar,
    Decision,
    TriggerState,
    compute_qty_at_fire,
    evaluate_chandelier_stop,
    evaluate_limit,
    evaluate_market,
    evaluate_stop,
    evaluate_stop_limit,
    evaluate_time_of_day,
    evaluate_trailing_stop,
    update_chandelier_state,
    update_trail_state,
)

LOG = logging.getLogger(__name__)

__all__ = [
    "ExitTriggerContext",
    "ExitTriggerHandler",
    "PreparedExit",
    "prepare_trigger_mask",
    "check_trigger_decision",
    "supported_trigger_kinds",
    "_EXIT_DISPATCH",
]


@dataclass
class ExitTriggerContext:
    """Bundle of every context field an exit-trigger handler might need."""

    position: Position
    bar: Bar
    is_close: bool = True
    trigger_state: TriggerState | None = None
    now: datetime | None = None
    scanner_eval_ctx: Any | None = None
    normalized_conditions: dict[str, Any] | None = None
    legacy_signed_offsets: bool = False


class ExitTriggerHandler(Protocol):
    """Signature every entry in :data:`_EXIT_DISPATCH` must satisfy."""

    def __call__(self, trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision: ...


def _no_fire(reason: str = "") -> Decision:
    return Decision(fire=False, reason=reason)


def _legacy_resolve_exit_price(trigger: ExitTrigger, position: Position) -> float | None:
    """Resolve strategy-tester legacy signed offsets for price exits.

    The live exit evaluator's canonical spec interprets ``offset_pct`` /
    ``offset_dollar`` as raw offsets from entry. Historical strategy-tester
    manifests used positive stop offsets to mean "away from entry in the
    adverse direction"; keep that compatibility behind an explicit context
    flag instead of a second dispatch table.
    """
    if trigger.price is not None:
        return float(trigger.price)

    side = Side.from_str(position.side)
    ref_price = float(position.avg_entry_price)
    leg_sign = 1.0 if trigger.kind is TriggerKind.LIMIT else -1.0

    if trigger.offset_pct is not None:
        pct = float(trigger.offset_pct) / 100.0
        return float(ref_price * (1.0 + side.sign * leg_sign * pct))
    if trigger.offset_dollar is not None:
        dollars = float(trigger.offset_dollar)
        return float(ref_price + side.sign * leg_sign * dollars)
    return None


def _legacy_limit(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    target = _legacy_resolve_exit_price(trigger, ctx.position)
    if target is None:
        return _no_fire("malformed limit (no price)")
    qty = compute_qty_at_fire(trigger, ctx.position)
    if qty <= 0:
        return _no_fire("position flat")
    if _legacy_price_touched(trigger.kind, ctx.position.side, ctx.bar, target):
        return Decision(fire=True, fire_price=target, qty=qty, reason="limit-touched-legacy")
    return _no_fire("limit not touched")


def _legacy_stop(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    stop = _legacy_resolve_exit_price(trigger, ctx.position)
    if stop is None:
        return _no_fire("malformed stop (no price)")
    qty = compute_qty_at_fire(trigger, ctx.position)
    if qty <= 0:
        return _no_fire("position flat")
    if _legacy_price_touched(trigger.kind, ctx.position.side, ctx.bar, stop):
        return Decision(fire=True, fire_price=stop, qty=qty, reason="stop-touched-legacy")
    return _no_fire("stop not touched")


def _legacy_price_touched(
    kind: TriggerKind, side: str, bar: Bar | BarSeries, target: float,
) -> bool | np.ndarray:
    """Same comparison for a scalar Bar or all-bars OHLC arrays."""
    is_long = Side.from_str(side).is_long
    if kind is TriggerKind.LIMIT:
        return (bar.high >= target) if is_long else (bar.low <= target)
    return (bar.low <= target) if is_long else (bar.high >= target)


def _legacy_stop_limit(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    stop_decision = _legacy_stop(trigger, ctx)
    if not stop_decision.fire:
        return stop_decision
    return Decision(
        fire=True,
        fire_price=stop_decision.fire_price,
        qty=stop_decision.qty,
        reason="stop-limit-legacy",
        limit_price=trigger.stop_limit_price,
    )


def _h_market(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    return evaluate_market(trigger, ctx.position, ctx.bar)


def _h_limit(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    if ctx.legacy_signed_offsets:
        return _legacy_limit(trigger, ctx)
    return evaluate_limit(trigger, ctx.position, ctx.bar)


def _h_stop(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    if ctx.legacy_signed_offsets:
        return _legacy_stop(trigger, ctx)
    return evaluate_stop(trigger, ctx.position, ctx.bar)


def _h_stop_limit(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    if ctx.legacy_signed_offsets:
        return _legacy_stop_limit(trigger, ctx)
    return evaluate_stop_limit(trigger, ctx.position, ctx.bar)


def _h_trailing_stop(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    state = ctx.trigger_state
    if state is None:
        return _no_fire("trailing_stop: no trigger_state")
    if trigger.trail_unit is None or trigger.trail_value is None:
        return _no_fire("trailing_stop: malformed")
    try:
        update_trail_state(state, trigger, ctx.position, ctx.bar, is_close=ctx.is_close)
        return evaluate_trailing_stop(state, trigger, ctx.position, ctx.bar)
    except Exception:  # noqa: BLE001
        LOG.exception(
            "exits.dispatch._h_trailing_stop: spec evaluator raised (trigger_id=%s)",
            trigger.id,
        )
        return _no_fire("trailing_stop: evaluator raised")


def _h_time_of_day(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    now = ctx.now or ctx.bar.date
    if now is None:
        return _no_fire("time_of_day: no datetime")
    try:
        return evaluate_time_of_day(trigger, ctx.position, ctx.bar, now=now)
    except Exception:  # noqa: BLE001
        LOG.exception(
            "exits.dispatch._h_time_of_day: spec evaluator raised (trigger_id=%s)",
            trigger.id,
        )
        return _no_fire("time_of_day: evaluator raised")


def _h_indicator(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    if not ctx.is_close and not trigger.evaluate_intrabar:
        return _no_fire("indicator: intrabar disabled")
    if ctx.scanner_eval_ctx is None:
        return _no_fire("indicator: no scanner context")
    if trigger.condition is None:
        return _no_fire("indicator: no condition")

    condition = trigger.condition
    if ctx.normalized_conditions is not None:
        condition = ctx.normalized_conditions.get(trigger.id, condition)
    try:
        result = _evaluate_group(condition, ctx.scanner_eval_ctx)
        evidence = list(getattr(ctx.scanner_eval_ctx, "evidence", []) or [])
    except NotImplementedError:
        return _no_fire("indicator: cross-interval not supported")
    except Exception:  # noqa: BLE001
        LOG.exception(
            "exits.dispatch._h_indicator: evaluate_group raised (trigger_id=%s)",
            trigger.id,
        )
        return _no_fire("indicator: evaluate_group raised")

    if result is True:
        return Decision(
            fire=True,
            fire_price=ctx.bar.close,
            qty=0.0,
            reason="indicator_true",
            evidence=evidence,
        )
    return _no_fire(f"indicator: result={result}")


def _h_chandelier(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    state = ctx.trigger_state
    if state is None:
        return _no_fire("chandelier: no trigger_state")
    try:
        is_activation = state.chandelier_frozen_params is None
        update_chandelier_state(
            state,
            trigger,
            ctx.position,
            ctx.bar,
            is_activation=is_activation,
        )
        return evaluate_chandelier_stop(state, trigger, ctx.position, ctx.bar)
    except Exception:  # noqa: BLE001
        LOG.exception(
            "exits.dispatch._h_chandelier: spec evaluator raised (trigger_id=%s)",
            trigger.id,
        )
        return _no_fire("chandelier: evaluator raised")


_EXIT_DISPATCH: dict[TriggerKind, ExitTriggerHandler] = {
    TriggerKind.MARKET: _h_market,
    TriggerKind.LIMIT: _h_limit,
    TriggerKind.STOP: _h_stop,
    TriggerKind.STOP_LIMIT: _h_stop_limit,
    TriggerKind.TRAILING_STOP: _h_trailing_stop,
    TriggerKind.TIME_OF_DAY: _h_time_of_day,
    TriggerKind.INDICATOR: _h_indicator,
    TriggerKind.CHANDELIER: _h_chandelier,
}


@dataclass(frozen=True)
class PreparedExitMask:
    """A close-bar indicator predicate tied to its canonical handler."""

    handler: ExitTriggerHandler
    values: np.ndarray

    def fires(self, trigger: ExitTrigger, index: int, position: Position) -> bool | None:
        if _EXIT_DISPATCH.get(trigger.kind) is not self.handler:
            return None
        return bool(self.values[index])


class PreparedExit(Protocol):
    def fires(self, trigger: ExitTrigger, index: int, position: Position) -> bool | None: ...


@dataclass
class _PreparedLegacyPrice:
    handler: ExitTriggerHandler
    bars: BarSeries
    position_key: tuple[str, float] | None = None
    target: float | None = None
    values: np.ndarray | None = None

    def fires(self, trigger: ExitTrigger, index: int, position: Position) -> bool | None:
        if _EXIT_DISPATCH.get(trigger.kind) is not self.handler:
            return None
        key = (position.side, position.avg_entry_price)
        if key != self.position_key:
            target = _legacy_resolve_exit_price(trigger, position)
            self.target = target
            self.values = None
            self.position_key = key
        if self.target is None:
            return False
        if compute_qty_at_fire(trigger, position) <= 0:
            return False
        if self.values is None:
            self.values = np.asarray(
                _legacy_price_touched(trigger.kind, position.side, self.bars, self.target), dtype=bool,
            )
        return bool(self.values[index])


def prepare_trigger_mask(
    trigger: ExitTrigger, *, bars: BarSeries, eval_ctx: Any,
    normalized_conditions: dict[str, Any],
    cache_prices: bool,
) -> PreparedExit | None:
    """Prepare mechanical legacy-price/indicator kernels for canonical handlers."""
    handler = _EXIT_DISPATCH.get(trigger.kind)
    if cache_prices and (
        (trigger.kind is TriggerKind.LIMIT and handler is _h_limit)
        or (trigger.kind is TriggerKind.STOP and handler is _h_stop)
        or (trigger.kind is TriggerKind.STOP_LIMIT and handler is _h_stop_limit)
    ):
        return _PreparedLegacyPrice(handler, bars)
    if handler is not _h_indicator or trigger.kind is not TriggerKind.INDICATOR:
        return None
    if eval_ctx is None or trigger.condition is None:
        return None
    condition = normalized_conditions.get(trigger.id, trigger.condition)
    masks = evaluate_group_vec(condition, eval_ctx)
    if masks is None:
        return None
    return PreparedExitMask(handler, masks[0])


def check_trigger_decision(trigger: ExitTrigger, ctx: ExitTriggerContext) -> Decision:
    """Evaluate ``trigger`` through the shared exit-dispatch registry."""
    handler = _EXIT_DISPATCH.get(trigger.kind)
    if handler is None:
        return _no_fire("unsupported trigger kind")
    return handler(trigger, ctx)


def supported_trigger_kinds() -> set[TriggerKind]:
    """Return trigger kinds currently present in the shared registry."""
    return set(_EXIT_DISPATCH)
