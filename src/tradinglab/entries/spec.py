"""Pure trigger-evaluation helpers for entry strategies.

Each function takes one bar and an :class:`EntryTrigger` and decides
whether the trigger fires. **Conventions for entries differ from exits**:

- LONG entry LIMIT (buy below market): fires when ``bar.low <= price``.
- SHORT entry LIMIT (sell above market): fires when ``bar.high >= price``.
- LONG entry STOP (buy-stop above market for breakouts): fires when
  ``bar.high >= stop_price``.
- SHORT entry STOP: fires when ``bar.low <= stop_price``.
- STOP_LIMIT: stop arms first, then becomes a LIMIT against the same bar.
  We only fire if both conditions hold within one bar; otherwise the
  trigger remains armed for the next bar (the evaluator is responsible
  for sustaining that state).

These conventions are the OPPOSITE of exit stops/limits. Documented
explicitly so the table of "did this fire?" decisions is auditable.

All return values are simple booleans. ``MARKET`` and ``INDICATOR``
triggers do NOT have a price-touched check — they're decided upstream
by the evaluator (MARKET fires on the next ``is_close=True`` bar after
arm; INDICATOR fires when the condition group evaluates True).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .model import Direction, EntryTrigger, TriggerKind

__all__ = [
    "BarLike",
    "should_fire_market",
    "should_fire_limit",
    "should_fire_stop",
    "should_fire_stop_limit",
    "trigger_fill_price",
]


@dataclass
class BarLike:
    """Tiny structural type for the bars passed in (mirrors Candle).

    Tests use this; production calls pass real
    :class:`tradinglab.data.types.Candle` objects whose attributes
    happen to match.
    """

    open: float
    high: float
    low: float
    close: float


def should_fire_market(trigger: EntryTrigger, bar: Any, *, is_close: bool) -> bool:
    """``MARKET`` triggers fire on the next CLOSED bar after arm.

    The evaluator is responsible for the "after arm" half — it only
    feeds bars to this function while the strategy is armed. We just
    enforce the closed-bar invariant.
    """
    if trigger.kind != TriggerKind.MARKET:
        return False
    return bool(is_close)


def should_fire_limit(
    trigger: EntryTrigger, bar: Any, *, direction: Direction,
) -> bool:
    """``LIMIT`` entry trigger.

    LONG: fires when bar.low <= price (buyer's price reached on a dip).
    SHORT: fires when bar.high >= price (seller's price reached on a rally).
    """
    if trigger.kind != TriggerKind.LIMIT:
        return False
    if trigger.price is None:
        return False
    if direction == Direction.LONG:
        return float(bar.low) <= float(trigger.price)
    return float(bar.high) >= float(trigger.price)


def should_fire_stop(
    trigger: EntryTrigger, bar: Any, *, direction: Direction,
) -> bool:
    """``STOP`` entry trigger (entry on breakout / breakdown).

    LONG: fires when bar.high >= stop_price (breakout above level).
    SHORT: fires when bar.low <= stop_price (breakdown below level).
    """
    if trigger.kind != TriggerKind.STOP:
        return False
    if trigger.stop_price is None:
        return False
    if direction == Direction.LONG:
        return float(bar.high) >= float(trigger.stop_price)
    return float(bar.low) <= float(trigger.stop_price)


def should_fire_stop_limit(
    trigger: EntryTrigger, bar: Any, *, direction: Direction,
    stop_already_armed: bool = False,
) -> bool:
    """``STOP_LIMIT`` entry trigger.

    Two-stage: the stop must be armed (high reached for LONG / low
    reached for SHORT), then within the same or a later bar the limit
    must also be reachable.

    ``stop_already_armed=False`` means we treat THIS bar as the
    arm-and-fill candidate (both conditions checked against this bar).
    ``stop_already_armed=True`` means a prior bar armed the stop and
    we're now just looking for the limit fill — only the limit half is
    checked against this bar.

    LONG: stop = bar.high >= stop_price; limit = bar.low <= price.
    SHORT: stop = bar.low <= stop_price; limit = bar.high >= price.
    """
    if trigger.kind != TriggerKind.STOP_LIMIT:
        return False
    if trigger.stop_price is None or trigger.price is None:
        return False
    if direction == Direction.LONG:
        stop_hit = float(bar.high) >= float(trigger.stop_price)
        limit_hit = float(bar.low) <= float(trigger.price)
    else:
        stop_hit = float(bar.low) <= float(trigger.stop_price)
        limit_hit = float(bar.high) >= float(trigger.price)
    if stop_already_armed:
        return limit_hit
    return stop_hit and limit_hit


# ---------------------------------------------------------------------------
# Fill-price selection
# ---------------------------------------------------------------------------


def trigger_fill_price(
    trigger: EntryTrigger,
    bar: Any,
    *,
    direction: Direction,
) -> Optional[float]:
    """Return the conservative fill price for a fired trigger.

    Mirrors the exits paper engine: at fire time we report a
    deterministic, non-optimistic fill price so backtests / sandbox
    runs are reproducible.

    - MARKET: bar.close.
    - LIMIT: trigger.price (we filled exactly at our price).
    - STOP: trigger.stop_price (slip is modelled separately if at all).
    - STOP_LIMIT: trigger.price (the limit half decides the print).
    - INDICATOR / SCANNER_ALERT: bar.close.
    """
    kind = trigger.kind
    if kind == TriggerKind.MARKET:
        return float(bar.close)
    if kind == TriggerKind.LIMIT:
        return float(trigger.price) if trigger.price is not None else None
    if kind == TriggerKind.STOP:
        return float(trigger.stop_price) if trigger.stop_price is not None else None
    if kind == TriggerKind.STOP_LIMIT:
        return float(trigger.price) if trigger.price is not None else None
    if kind in (TriggerKind.INDICATOR, TriggerKind.SCANNER_ALERT):
        return float(bar.close)
    return None
