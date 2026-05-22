"""Pure-function evaluators for native (non-indicator) exit triggers.

This module is **stateful only via the explicit** :class:`TriggerState`
parameter. The evaluator (``exits/evaluator.py``, separate slice) owns
the actual :class:`TriggerState` instances per ``(position_id, trigger_id)``
pair and threads them through these functions.

Why split it this way? Two reasons:

1. The HWM / activation / corrected-bar logic of trailing stops is the
   single hairiest piece of state in the exit-strategies feature and
   benefits from being individually testable on plain dataclasses.
2. The evaluator slice has *much* more surface area (orchestration,
   sinks, audit log) and would otherwise drown the trail logic in
   noise.

Coordinate conventions
----------------------

- A "fire" returns :class:`Decision` with ``fire=True`` and a
  ``fire_price`` (the price at which the order should be sent — for
  market this is the bar's *current* price, for limit/stop it's the
  configured trigger price snapped to the touched-through value).
- ``Decision.qty`` is the absolute quantity to exit, resolved at fire
  time as ``trigger.qty_pct / 100 * position.qty_open`` (B6 fix).
- "Touched-through" detection uses ``bar.high`` / ``bar.low``: a long
  stop at $180 fires the moment ``bar.low <= 180``, regardless of
  ``bar.close``. ``fire_price`` for a stop is the configured stop
  price (slippage is not modeled here — :class:`PaperBrokerEngine`
  applies a fixed bps fudge in a later slice).
- Trailing stops respect the :class:`TrailBasis` toggle: ``CLOSE``
  basis updates HWM only on bar-close events; ``INTRABAR`` updates on
  every tick. The activation gate must be satisfied (peak excursion
  ≥ activation_value in activation_unit) before the trail is "armed".
- ``CHART_EQUITY`` retroactive correction (Schwab streams) — when the
  evaluator detects a bar-correction, it calls
  :func:`recompute_hwm_from_history` to discard now-invalid retroactive
  highs.

Out of scope (for this module)
------------------------------

- Indicator triggers (``TriggerKind.INDICATOR``) — those evaluate
  scanner expressions over a ``BarsRegistry`` and live in the evaluator
  slice.
- Order placement / sink dispatch.
- Audit log writes.
- Multi-leg OCO cancellation. Each function here decides only whether
  *one* trigger fires.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Sequence

from ..positions.model import Position
from .model import (
    ActivationUnit,
    ExitTrigger,
    TrailBasis,
    TrailUnit,
    TriggerKind,
)

__all__ = [
    "TriggerState",
    "Decision",
    "Bar",
    "evaluate_market",
    "resolve_price",
    "evaluate_limit",
    "evaluate_stop",
    "evaluate_stop_limit",
    "update_trail_state",
    "evaluate_trailing_stop",
    "evaluate_time_of_day",
    "compute_qty_at_fire",
    "recompute_hwm_from_history",
    "compute_initial_risk_per_share",
    "update_chandelier_state",
    "evaluate_chandelier_stop",
    "freeze_chandelier_params",
]


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass
class Bar:
    """Minimal OHLC bar shape used by these evaluators.

    ``Candle`` from the main models module is compatible (duck-typed):
    we only read ``open``, ``high``, ``low``, ``close``, ``volume`` and
    optionally ``date``.
    """

    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    date: Optional[datetime] = None


@dataclass
class TriggerState:
    """Mutable per-trigger runtime state.

    Owned by the evaluator; this module reads + writes via plain field
    access. Persisted across crashes via positions/storage so a long
    trail's HWM survives a restart.
    """

    armed: bool = True
    # Trailing-stop fields:
    hwm: Optional[float] = None     # highest favorable price observed
    lwm: Optional[float] = None     # lowest favorable price observed (shorts)
    activated: bool = False         # activation gate passed?
    trail_price: Optional[float] = None  # current armed stop price
    # Chandelier-stop fields (entry-anchored rolling window, Camp B):
    #
    # * ``chandelier_rolling_high`` / ``chandelier_rolling_low`` track
    #   the running extremum within the lookback window since
    #   activation. Updated bar-by-bar in :func:`update_chandelier_state`.
    # * ``chandelier_window_count`` counts how many bars have been seen
    #   since activation, so we can stop growing the window once it
    #   hits ``lookback``.
    # * ``chandelier_stop`` is the ratcheted stop price (long: monotone
    #   non-decreasing; short: monotone non-increasing).
    # * ``chandelier_atr_state`` is a thin internal state for the
    #   running ATR computation; structure is opaque to callers.
    # * ``chandelier_frozen_params`` snapshots the
    #   ``(lookback, atr_period, multiplier, ma_type)`` tuple at
    #   activation so subsequent template edits cannot retroactively
    #   change the stop math on an open position.
    # * ``chandelier_realized_slippage`` is the unfavourable gap-fill
    #   delta surfaced on the most recent fire: positive value in
    #   dollars per share = trader got worse than stop. Zero when the
    #   stop was touched cleanly within the bar.
    chandelier_rolling_high: Optional[float] = None
    chandelier_rolling_low: Optional[float] = None
    chandelier_window_count: int = 0
    chandelier_stop: Optional[float] = None
    chandelier_atr_state: Optional[Dict[str, Any]] = None
    chandelier_frozen_params: Optional[Dict[str, Any]] = None
    chandelier_realized_slippage: float = 0.0
    # General:
    last_evaluated_bar_ts: Optional[datetime] = None
    fire_count: int = 0


@dataclass
class Decision:
    """Result of a single trigger evaluation.

    A ``no-fire`` decision is `Decision(fire=False)` with the other
    fields meaningless.
    """

    fire: bool
    fire_price: float = 0.0
    qty: float = 0.0
    reason: str = ""
    # For stop-limit: the limit price for the order body, distinct
    # from the stop trigger price.
    limit_price: Optional[float] = None
    # Within-last-N-bars look-back evidence collected by the engine
    # walk during INDICATOR triggers. Each entry is a
    # :class:`scanner.model.MatchEvidence` describing a node that
    # fired and the bar offset it fired on. Empty on no-fire decisions
    # and on decisions whose triggers don't use a within-last walk.
    # Typed loosely (``List[Any]``) here to avoid pulling
    # scanner.model into the spec module's import surface.
    evidence: List[Any] = field(default_factory=list)


def _no_fire(reason: str = "") -> Decision:
    return Decision(fire=False, reason=reason)


# ---------------------------------------------------------------------------
# Price resolution
# ---------------------------------------------------------------------------


def resolve_price(
    trigger: ExitTrigger,
    position: Position,
    *,
    use_stop_limit: bool = False,
) -> Optional[float]:
    """Resolve an absolute trigger price from
    ``price | offset_pct | offset_dollar``.

    Returns ``None`` if the trigger is malformed (no price field set).
    Returns the absolute price otherwise.

    For limit / stop / stop_limit the offset is interpreted relative to
    ``position.avg_entry_price``. The sign convention is *raw*:
    ``offset_pct=+2`` means +2% of entry price (above for either side),
    ``offset_dollar=-1.5`` means $1.50 below entry. Long-vs-short
    appropriateness is the user's responsibility — the GUI shows arrows
    to reduce mistakes, and the evaluator applies touched-through
    semantics for stops regardless.

    When ``use_stop_limit=True``, reads the ``stop_limit_*`` fields
    instead. ``stop_limit_offset`` is interpreted relative to the
    *stop trigger price*, not the entry price (more useful in practice).
    """
    if use_stop_limit:
        if trigger.stop_limit_price is not None:
            return float(trigger.stop_limit_price)
        if trigger.stop_limit_offset is not None:
            stop_px = resolve_price(trigger, position, use_stop_limit=False)
            if stop_px is None:
                return None
            return stop_px + float(trigger.stop_limit_offset)
        return None

    if trigger.price is not None:
        return float(trigger.price)
    if trigger.offset_pct is not None:
        return position.avg_entry_price * (1.0 + float(trigger.offset_pct) / 100.0)
    if trigger.offset_dollar is not None:
        return position.avg_entry_price + float(trigger.offset_dollar)
    return None


# ---------------------------------------------------------------------------
# Quantity resolution at fire time (B6 fix)
# ---------------------------------------------------------------------------


def compute_qty_at_fire(trigger: ExitTrigger, position: Position) -> float:
    """Resolve absolute fire quantity from ``qty_pct`` against current
    ``position.qty_open``.

    Returns 0.0 if the position is already flat. Quantity is left as a
    raw ``float`` here — fractional shares are caller's call.
    """
    if position.qty_open <= 0:
        return 0.0
    pct = float(trigger.qty_pct) / 100.0
    return position.qty_open * pct


# ---------------------------------------------------------------------------
# Native-kind evaluators
# ---------------------------------------------------------------------------


def evaluate_market(trigger: ExitTrigger, position: Position, bar: Bar) -> Decision:
    """A market trigger fires immediately on arming.

    The price reported is ``bar.close`` (closest representation of
    "now"). PaperBrokerEngine's slippage layer handles the actual fill
    deviation in a later slice.
    """
    if trigger.kind != TriggerKind.MARKET:
        return _no_fire("kind mismatch")
    qty = compute_qty_at_fire(trigger, position)
    if qty <= 0:
        return _no_fire("position flat")
    return Decision(fire=True, fire_price=bar.close, qty=qty, reason="market")


def evaluate_limit(trigger: ExitTrigger, position: Position, bar: Bar) -> Decision:
    """Touched-through limit detection.

    For a long position (selling at a limit *above* entry), fires when
    ``bar.high >= limit_price``. For a short (buying at a limit
    *below* entry), fires when ``bar.low <= limit_price``.
    """
    if trigger.kind != TriggerKind.LIMIT:
        return _no_fire("kind mismatch")
    px = resolve_price(trigger, position)
    if px is None:
        return _no_fire("malformed limit (no price)")
    qty = compute_qty_at_fire(trigger, position)
    if qty <= 0:
        return _no_fire("position flat")
    if position.side == "long" and bar.high >= px:
        return Decision(fire=True, fire_price=px, qty=qty, reason="limit-touched-up")
    if position.side == "short" and bar.low <= px:
        return Decision(fire=True, fire_price=px, qty=qty, reason="limit-touched-down")
    return _no_fire("limit not touched")


def evaluate_stop(trigger: ExitTrigger, position: Position, bar: Bar) -> Decision:
    """Touched-through stop detection.

    For a long, stop is below entry; fires on ``bar.low <= stop_price``.
    For a short, stop is above entry; fires on ``bar.high >= stop_price``.
    Gap-through stops fire at ``bar.open`` not at the stop price,
    reflecting realistic fill slippage on a gap.
    """
    if trigger.kind != TriggerKind.STOP:
        return _no_fire("kind mismatch")
    px = resolve_price(trigger, position)
    if px is None:
        return _no_fire("malformed stop (no price)")
    qty = compute_qty_at_fire(trigger, position)
    if qty <= 0:
        return _no_fire("position flat")

    if position.side == "long":
        if bar.low <= px:
            # Gap-through: open already below stop → fill at open
            fire_at = min(px, bar.open) if bar.open <= px else px
            return Decision(fire=True, fire_price=fire_at, qty=qty, reason="stop-touched-down")
    else:  # short
        if bar.high >= px:
            fire_at = max(px, bar.open) if bar.open >= px else px
            return Decision(fire=True, fire_price=fire_at, qty=qty, reason="stop-touched-up")
    return _no_fire("stop not touched")


def evaluate_stop_limit(trigger: ExitTrigger, position: Position, bar: Bar) -> Decision:
    """Stop-limit: stop trigger arms a limit order.

    Fires when the stop is touched AND the bar's price range covers
    the limit price. If the bar gaps so far that the limit is missed
    (e.g. long stop-limit with stop=180, limit=179.50; bar drops to
    178 without trading 179.50 on the way down), this returns ``no
    fire`` — the order would sit unfilled in real life. Caller may
    re-evaluate next bar.
    """
    if trigger.kind != TriggerKind.STOP_LIMIT:
        return _no_fire("kind mismatch")
    stop_px = resolve_price(trigger, position, use_stop_limit=False)
    limit_px = resolve_price(trigger, position, use_stop_limit=True)
    if stop_px is None or limit_px is None:
        return _no_fire("malformed stop-limit")
    qty = compute_qty_at_fire(trigger, position)
    if qty <= 0:
        return _no_fire("position flat")

    if position.side == "long":
        # Long stop-limit: stop below entry, limit at-or-below stop.
        # Stop touched if low <= stop_px. Limit fillable if the bar's
        # range covers limit_px after the stop is hit.
        if bar.low <= stop_px:
            # If gap already opened below limit, no fill (order sits).
            if bar.open < limit_px:
                return _no_fire("gap-through limit (long)")
            if bar.high >= limit_px:
                return Decision(
                    fire=True,
                    fire_price=limit_px,
                    qty=qty,
                    reason="stop-limit-long",
                    limit_price=limit_px,
                )
        return _no_fire("stop-limit (long) not triggered")
    # short
    if bar.high >= stop_px:
        if bar.open > limit_px:
            return _no_fire("gap-through limit (short)")
        if bar.low <= limit_px:
            return Decision(
                fire=True,
                fire_price=limit_px,
                qty=qty,
                reason="stop-limit-short",
                limit_price=limit_px,
            )
    return _no_fire("stop-limit (short) not triggered")


# ---------------------------------------------------------------------------
# Trailing stop
# ---------------------------------------------------------------------------


def compute_initial_risk_per_share(
    position: Position,
    paired_stop_price: Optional[float],
) -> Optional[float]:
    """R-multiple denominator: ``|entry - paired_stop|``.

    Returns ``None`` if no paired stop was given (caller should fall
    back to ``activation_unit=PERCENT`` semantics or refuse-arm).
    """
    if paired_stop_price is None or paired_stop_price <= 0:
        return None
    risk = abs(position.avg_entry_price - paired_stop_price)
    return risk if risk > 0 else None


def update_trail_state(
    state: TriggerState,
    trigger: ExitTrigger,
    position: Position,
    bar: Bar,
    *,
    is_close: bool,
    atr_value: Optional[float] = None,
    paired_stop_price: Optional[float] = None,
) -> None:
    """Update HWM/LWM, activation, and trail_price *in place* on ``state``.

    Caller drives this on every relevant evaluation point:

    - ``trail_basis=INTRABAR``: call on every tick (forming or close)
      with ``is_close=False`` for forming bars and ``is_close=True``
      for actual close events.
    - ``trail_basis=CLOSE``: call only on close events with
      ``is_close=True``. Caller may safely call with ``is_close=False``
      and this function will skip the HWM update (defensive).

    On bar-correction events (Schwab CHART_EQUITY retroactive overwrite),
    the evaluator should call :func:`recompute_hwm_from_history` instead
    of this function for the corrected bar.
    """
    if trigger.kind != TriggerKind.TRAILING_STOP:
        return
    if trigger.trail_unit is None or trigger.trail_value is None:
        return

    # HWM gate per trail_basis:
    if trigger.trail_basis == TrailBasis.CLOSE and not is_close:
        return  # never update HWM on forming when basis=close

    # The "favorable" extreme is high for longs, low for shorts.
    # Use the bar's high/low (intrabar) or close (defensive on
    # close-only basis when low/high may not be present).
    if trigger.trail_basis == TrailBasis.INTRABAR:
        favorable = bar.high if position.side == "long" else bar.low
    else:  # CLOSE
        favorable = bar.close

    if position.side == "long":
        if state.hwm is None or favorable > state.hwm:
            state.hwm = favorable
    else:
        if state.lwm is None or favorable < state.lwm:
            state.lwm = favorable

    # Activation gate.
    if not state.activated and trigger.activation_unit is not None and trigger.activation_value is not None:
        peak = state.hwm if position.side == "long" else state.lwm
        if peak is not None:
            if _activation_satisfied(
                trigger.activation_unit,
                trigger.activation_value,
                position,
                peak,
                paired_stop_price,
            ):
                state.activated = True
    elif trigger.activation_unit is None or trigger.activation_value is None:
        # No activation gate ⇒ always armed
        state.activated = True

    # Trail price = HWM/LWM offset by trail_value/trail_unit, but only
    # while activated.
    if state.activated:
        anchor = state.hwm if position.side == "long" else state.lwm
        if anchor is not None:
            offset = _trail_offset_abs(trigger.trail_unit, trigger.trail_value, anchor, atr_value)
            if offset is not None:
                proposed = anchor - offset if position.side == "long" else anchor + offset
                # Ratchet: never loosen the trail once tighter.
                if state.trail_price is None:
                    state.trail_price = proposed
                else:
                    if position.side == "long":
                        state.trail_price = max(state.trail_price, proposed)
                    else:
                        state.trail_price = min(state.trail_price, proposed)


def evaluate_trailing_stop(
    state: TriggerState,
    trigger: ExitTrigger,
    position: Position,
    bar: Bar,
) -> Decision:
    """Fires when ``bar`` violates the current ``state.trail_price``.

    Long: fire when ``bar.low <= trail_price`` (touched-through).
    Short: fire when ``bar.high >= trail_price``.

    Caller must have already called :func:`update_trail_state` for
    this bar with the correct ``is_close`` flag — this function does
    not mutate state.
    """
    if trigger.kind != TriggerKind.TRAILING_STOP:
        return _no_fire("kind mismatch")
    if not state.activated or state.trail_price is None:
        return _no_fire("trail not yet activated")
    qty = compute_qty_at_fire(trigger, position)
    if qty <= 0:
        return _no_fire("position flat")
    px = state.trail_price
    if position.side == "long" and bar.low <= px:
        # Gap-through: open already below trail → fill at open
        fire_at = min(px, bar.open) if bar.open <= px else px
        return Decision(fire=True, fire_price=fire_at, qty=qty, reason="trailing-stop-long")
    if position.side == "short" and bar.high >= px:
        fire_at = max(px, bar.open) if bar.open >= px else px
        return Decision(fire=True, fire_price=fire_at, qty=qty, reason="trailing-stop-short")
    return _no_fire("trail not touched")


def recompute_hwm_from_history(
    state: TriggerState,
    trigger: ExitTrigger,
    position: Position,
    bars: Sequence[Bar],
    *,
    atr_values: Optional[Sequence[Optional[float]]] = None,
    paired_stop_price: Optional[float] = None,
) -> None:
    """Reseed ``state.hwm`` (longs) / ``state.lwm`` (shorts) from a
    sequence of historical bars.

    Used when a corrected-bar event (Schwab CHART_EQUITY retroactive
    overwrite) invalidates an earlier intrabar high. The caller hands
    in the full known-good bar history since the position opened
    (or any "since last forming bar" window). This function recomputes
    HWM/LWM from those bars under the trigger's current ``trail_basis``.
    """
    if trigger.kind != TriggerKind.TRAILING_STOP:
        return
    state.hwm = None
    state.lwm = None
    state.activated = False
    state.trail_price = None
    for idx, bar in enumerate(bars):
        atr_val = atr_values[idx] if (atr_values is not None and idx < len(atr_values)) else None
        update_trail_state(
            state,
            trigger,
            position,
            bar,
            is_close=True,
            atr_value=atr_val,
            paired_stop_price=paired_stop_price,
        )


# ---------------------------------------------------------------------------
# Time-of-day
# ---------------------------------------------------------------------------


def evaluate_time_of_day(
    trigger: ExitTrigger,
    position: Position,
    bar: Bar,
    *,
    now: datetime,
) -> Decision:
    """Fires when ``now.time() >= trigger.time_of_day``.

    Edge-triggered semantics live in the evaluator slice (this
    function is level-triggered): once the wall-clock crosses the
    cutoff, every subsequent evaluation fires until the position is
    flat or the trigger is disarmed. The evaluator de-dupes via the
    ``fire_count`` field of :class:`TriggerState`.
    """
    if trigger.kind != TriggerKind.TIME_OF_DAY:
        return _no_fire("kind mismatch")
    if not trigger.time_of_day:
        return _no_fire("malformed time_of_day")
    try:
        h, m = trigger.time_of_day.split(":")
        cutoff = time(hour=int(h), minute=int(m))
    except (ValueError, AttributeError):
        return _no_fire("malformed time_of_day")
    qty = compute_qty_at_fire(trigger, position)
    if qty <= 0:
        return _no_fire("position flat")
    if now.time() >= cutoff:
        return Decision(fire=True, fire_price=bar.close, qty=qty, reason="time-of-day")
    return _no_fire("before cutoff")


# ---------------------------------------------------------------------------
# Chandelier stop
# ---------------------------------------------------------------------------
#
# A chandelier stop is a volatility-trailing exit anchored at the entry
# bar (Camp B), ratcheted forward, and fired on touch (low ≤ stop for
# longs, high ≥ stop for shorts).
#
# Long stop  = highest_high(window) − multiplier × ATR
# Short stop = lowest_low(window)   + multiplier × ATR
#
# The window is seeded at the entry bar and expands forward, capped at
# ``chandelier_lookback`` bars. Pre-entry bars are NEVER consulted by
# this in-trade evaluator — that's the difference between an exit-rule
# chandelier and the always-on indicator overlay (which has no anchor).
#
# Parameters are FROZEN at activation by the evaluator via
# :func:`freeze_chandelier_params`; the dict is stashed on
# :attr:`TriggerState.chandelier_frozen_params` and never re-read from
# the trigger. This makes mid-trade template edits a no-op on live
# positions (matches the user's locked design and broker reality).
#
# Math is delegated to :mod:`core.chandelier_math` so the indicator and
# the exit rule share a single source of truth.


def freeze_chandelier_params(trigger: ExitTrigger) -> Dict[str, Any]:
    """Build the frozen-params dict from a fresh chandelier trigger.

    Called by the evaluator once at activation. The result is stashed
    on :attr:`TriggerState.chandelier_frozen_params` and used by
    :func:`update_chandelier_state` and :func:`evaluate_chandelier_stop`
    on every subsequent bar.
    """
    return {
        "lookback": int(trigger.chandelier_lookback),
        "atr_period": int(trigger.chandelier_atr_period),
        "multiplier": float(trigger.chandelier_multiplier),
        "ma_type": str(trigger.chandelier_ma_type).upper(),
    }


# Internal: per-kernel running-ATR state. Kept lightweight because
# we maintain one per active chandelier trigger. The ATR is computed
# fresh on every bar from the small TR history slice (capped at
# ``atr_period + 1`` bars) — this keeps state tiny and the math
# trivially correct without re-implementing RMA / SMA / EMA / WMA
# recurrences inline.


def _update_chandelier_atr(
    state: "TriggerState",
    bar: Bar,
    *,
    atr_period: int,
    ma_type: str,
) -> Optional[float]:
    """Advance the running ATR by one bar; return the current ATR value
    (or ``None`` while warming up).

    Internal helper for :func:`update_chandelier_state`. State is
    stashed in ``state.chandelier_atr_state`` as a small dict::

        {
            "prev_close": Optional[float],
            "tr_history": List[float],   # length capped at atr_period
        }

    The True Range for bar 1 (no prior close) is NaN — represented as
    ``None`` here and excluded from the TR history. ATR is returned
    once ``len(tr_history) >= atr_period``.
    """
    s = state.chandelier_atr_state
    if s is None:
        s = {"prev_close": None, "tr_history": []}
        state.chandelier_atr_state = s

    prev_close = s.get("prev_close")
    if prev_close is None:
        # First bar: no prior close ⇒ TR is undefined. Store close
        # and return None.
        s["prev_close"] = float(bar.close)
        return None

    hl = float(bar.high) - float(bar.low)
    hpc = abs(float(bar.high) - float(prev_close))
    lpc = abs(float(bar.low) - float(prev_close))
    tr = max(hl, hpc, lpc)
    s["prev_close"] = float(bar.close)

    history: List[float] = s["tr_history"]
    history.append(tr)
    if len(history) > int(atr_period):
        # Keep only the most recent atr_period values for the running
        # window. Wilder's RMA and EMA mathematically use all history,
        # but in practice the impact of values older than ~3×period is
        # below 1e-3 — and keeping the buffer bounded matters for
        # long-running positions on intraday timeframes.
        del history[: len(history) - int(atr_period)]

    if len(history) < int(atr_period):
        return None

    import numpy as np

    from ..indicators.ma_kernels import apply_ma

    arr = np.asarray(history, dtype=np.float64)
    smoothed = apply_ma(str(ma_type).upper(), arr, int(atr_period))
    last = float(smoothed[-1])
    if not math.isfinite(last):
        return None
    return last


def update_chandelier_state(
    state: TriggerState,
    trigger: ExitTrigger,
    position: Position,
    bar: Bar,
    *,
    is_activation: bool = False,
) -> None:
    """Update the chandelier state by one bar.

    On the **activation bar** (the entry bar) call with
    ``is_activation=True``. This seeds the rolling extremum from this
    bar's high/low and snapshots the frozen params onto the state.

    On every **subsequent bar** call with ``is_activation=False``. This
    advances the rolling-high (longs) or rolling-low (shorts) window,
    advances the running ATR, and recomputes the ratcheted stop.

    The stop never widens (longs: monotone non-decreasing; shorts:
    monotone non-increasing).

    Does nothing for non-chandelier triggers.
    """
    if trigger.kind != TriggerKind.CHANDELIER:
        return

    # Freeze params on the activation bar so subsequent template edits
    # cannot retroactively change the math.
    if is_activation or state.chandelier_frozen_params is None:
        state.chandelier_frozen_params = freeze_chandelier_params(trigger)

    params = state.chandelier_frozen_params
    lookback = int(params["lookback"])
    atr_period = int(params["atr_period"])
    multiplier = float(params["multiplier"])
    ma_type = str(params["ma_type"])

    # Track the rolling extremum window (Camp B: seeded at entry,
    # expanding forward, capped at lookback). state.chandelier_window_count
    # is the number of bars since activation, capped at lookback.
    side = position.side
    h = float(bar.high)
    l = float(bar.low)

    if is_activation:
        # Reset window on activation. The entry bar itself is the
        # first bar in the window.
        state.chandelier_window_count = 1
        state.chandelier_rolling_high = h if side == "long" else None
        state.chandelier_rolling_low = l if side == "short" else None
        state.chandelier_stop = None  # don't fire on the entry bar
        # Reset ATR running state on activation to avoid bleed from a
        # previous attachment lifecycle. Then seed with the entry bar's
        # close (so the next bar's TR can compute).
        state.chandelier_atr_state = {
            "prev_close": float(bar.close),
            "tr_history": [],
        }
        return

    # Advance window count, capped at lookback.
    state.chandelier_window_count = min(
        int(state.chandelier_window_count) + 1, lookback
    )

    if side == "long":
        prev_high = state.chandelier_rolling_high
        if prev_high is None or h > prev_high:
            state.chandelier_rolling_high = h
        # Note: when the window has grown past `lookback`, *new* highs
        # within the window can still raise the rolling-high, but old
        # highs that drop out of the trailing window can no longer
        # lower it (the stop is ratchet-protected anyway). This is the
        # standard chandelier behaviour and matches what the trader
        # memo specified: ratcheting is the defining trait, so we
        # never widen the stop even when the window slides.
    else:
        prev_low = state.chandelier_rolling_low
        if prev_low is None or l < prev_low:
            state.chandelier_rolling_low = l

    # Advance ATR.
    atr = _update_chandelier_atr(
        state, bar, atr_period=atr_period, ma_type=ma_type
    )
    if atr is None or not math.isfinite(atr):
        # Still warming up; no stop yet. Don't ratchet a NaN.
        return

    if side == "long":
        anchor = state.chandelier_rolling_high
        if anchor is None:
            return
        proposed = float(anchor) - multiplier * float(atr)
        if state.chandelier_stop is None:
            state.chandelier_stop = proposed
        else:
            # Ratchet UP only.
            if proposed > state.chandelier_stop:
                state.chandelier_stop = proposed
    else:
        anchor = state.chandelier_rolling_low
        if anchor is None:
            return
        proposed = float(anchor) + multiplier * float(atr)
        if state.chandelier_stop is None:
            state.chandelier_stop = proposed
        else:
            # Ratchet DOWN only.
            if proposed < state.chandelier_stop:
                state.chandelier_stop = proposed


def evaluate_chandelier_stop(
    state: TriggerState,
    trigger: ExitTrigger,
    position: Position,
    bar: Bar,
) -> Decision:
    """Fires when the bar touches the current ratcheted chandelier stop.

    Touch semantics (matches real broker stop orders):
      - long: fire when ``bar.low <= state.chandelier_stop``
      - short: fire when ``bar.high >= state.chandelier_stop``

    Gap handling: when ``bar.open`` is worse than the stop (long: open
    below stop; short: open above stop), fill is recorded at the stop
    level and the unfavourable slippage is stashed on
    ``state.chandelier_realized_slippage`` (positive dollars per share)
    so the evaluator can surface it on the fire event.

    Caller must have already called :func:`update_chandelier_state` for
    this bar — this function does not mutate runtime state.

    Returns ``no-fire`` if the stop has not yet been computed (warm-up).
    """
    if trigger.kind != TriggerKind.CHANDELIER:
        return _no_fire("kind mismatch")
    stop = state.chandelier_stop
    if stop is None or not math.isfinite(float(stop)):
        return _no_fire("chandelier warming up")
    qty = compute_qty_at_fire(trigger, position)
    if qty <= 0:
        return _no_fire("position flat")

    if position.side == "long":
        if bar.low <= stop:
            # Fill at stop level per the user's locked design. Surface
            # realized slippage when the open was worse than the stop.
            slippage = max(0.0, float(stop) - float(bar.open)) if bar.open < stop else 0.0
            state.chandelier_realized_slippage = slippage
            return Decision(
                fire=True,
                fire_price=float(stop),
                qty=qty,
                reason="chandelier-long",
            )
    else:  # short
        if bar.high >= stop:
            slippage = max(0.0, float(bar.open) - float(stop)) if bar.open > stop else 0.0
            state.chandelier_realized_slippage = slippage
            return Decision(
                fire=True,
                fire_price=float(stop),
                qty=qty,
                reason="chandelier-short",
            )
    return _no_fire("chandelier not touched")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trail_offset_abs(
    unit: TrailUnit,
    value: float,
    anchor_price: float,
    atr_value: Optional[float],
) -> Optional[float]:
    """Convert (unit, value) → absolute offset in dollars.

    Returns ``None`` if the unit is ATR but no atr_value was provided.
    """
    if unit == TrailUnit.PERCENT:
        return anchor_price * (float(value) / 100.0)
    if unit == TrailUnit.DOLLAR:
        return float(value)
    if unit == TrailUnit.ATR:
        if atr_value is None or not math.isfinite(atr_value):
            return None
        return float(value) * float(atr_value)
    return None


def _activation_satisfied(
    unit: ActivationUnit,
    value: float,
    position: Position,
    peak_price: float,
    paired_stop_price: Optional[float],
) -> bool:
    """Has the position's peak excursion crossed the activation threshold?"""
    if unit == ActivationUnit.PERCENT:
        if position.side == "long":
            return peak_price >= position.avg_entry_price * (1.0 + float(value) / 100.0)
        return peak_price <= position.avg_entry_price * (1.0 - float(value) / 100.0)
    if unit == ActivationUnit.DOLLAR:
        if position.side == "long":
            return peak_price >= position.avg_entry_price + float(value)
        return peak_price <= position.avg_entry_price - float(value)
    if unit == ActivationUnit.R_MULTIPLE:
        risk = compute_initial_risk_per_share(position, paired_stop_price)
        if risk is None:
            return False
        if position.side == "long":
            return peak_price >= position.avg_entry_price + float(value) * risk
        return peak_price <= position.avg_entry_price - float(value) * risk
    return False
