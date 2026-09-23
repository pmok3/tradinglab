"""Optional decision masks consumed by the evaluator's single scalar loop.

Dispatch owns mask support. Unsupported/custom handlers and condition trees
use scalar dispatch; position-dependent exits always use the canonical rules.
No orders, counters, activation, sizing or exit targets are cached here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..backtest.bars import BarSeries
from ..constants import is_intraday
from ..core.timezones import parse_hhmm
from ..entries.dispatch import PreparedEntryMask
from ..entries.dispatch import prepare_trigger_mask as prepare_entry_mask
from ..entries.model import EntryStrategy, PositionAlreadyOpenPolicy
from ..exits.dispatch import PreparedExit
from ..exits.dispatch import prepare_trigger_mask as prepare_exit_mask
from ..exits.model import ExitStrategy
from ..positions.model import Position
from ..scanner.engine import EvaluationContext
from ..scanner.model import Group


@dataclass
class VectorEvalPlan:
    bars: BarSeries
    entry_gate: np.ndarray
    entry: PreparedEntryMask | None
    exits: dict[tuple[int, int], PreparedExit]
    position: Position | None = None
    position_key: tuple[str, float, float, int] | None = None


def build_plan(
    *,
    n: int,
    bars: BarSeries,
    interval: str,
    entry_strategy: EntryStrategy,
    exit_strategy: ExitStrategy,
    et_tod_sec: np.ndarray,
    rth_mask: np.ndarray,
    eval_ctx: EvaluationContext | None,
    normalized_conditions: dict[str, Group],
) -> VectorEvalPlan:
    gate = np.ones(n, dtype=bool)
    intraday = is_intraday(interval) if interval else True
    if intraday:
        start = parse_hhmm(entry_strategy.arm_window_start)
        end = parse_hhmm(entry_strategy.arm_window_end)
        if start is not None and end is not None:
            s = start.hour * 3600 + start.minute * 60
            e = end.hour * 3600 + end.minute * 60
            if s <= e:
                gate &= (et_tod_sec >= s) & (et_tod_sec <= e)
            else:
                gate &= (et_tod_sec >= s) | (et_tod_sec <= e)
        if entry_strategy.require_market_open:
            gate &= rth_mask

    entry = prepare_entry_mask(
        entry_strategy.trigger, n=n, eval_ctx=eval_ctx,
        normalized_conditions=normalized_conditions,
    )
    exits: dict[tuple[int, int], PreparedExit] = {}
    for leg_idx, leg in enumerate(exit_strategy.legs):
        if not leg.enabled:
            continue
        for trigger_idx, trigger in enumerate(leg.triggers):
            if not trigger.enabled:
                continue
            mask = prepare_exit_mask(
                trigger, bars=bars, eval_ctx=eval_ctx,
                normalized_conditions=normalized_conditions,
                cache_prices=entry_strategy.position_already_open_policy is PositionAlreadyOpenPolicy.BLOCK,
            )
            if mask is not None:
                exits[leg_idx, trigger_idx] = mask
    return VectorEvalPlan(bars=bars, entry_gate=gate, entry=entry, exits=exits)
