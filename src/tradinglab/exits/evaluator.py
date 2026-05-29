"""Live exit-strategy evaluator.

Owns the per-position trigger state machine. Sits between the
:class:`PositionTracker` (mutable position state), the bar/tick
stream (live price feed), the :class:`BarsRegistry` (cross-interval
indicator views), and a :class:`ExitSignalSink` (paper / manual /
broker output).

Responsibilities (per the v1 plan, Layer 5)
-------------------------------------------

1. **Strategy attachment.** :meth:`attach_strategy` binds an
   :class:`ExitStrategy` to a position, allocates a per-trigger
   :class:`_TriggerSlot` (armed flag, trail state, last-fire dedup
   key), audits ``strategy_attach``.
   2. **Per-bar evaluation.** :meth:`on_bar` walks every leg / trigger
   for the bound strategy and evaluates each via the shared
   :mod:`exits.dispatch` registry. Returns the list of fired
   :class:`ExitSignal` instances after they have been submitted to
   the sink (so callers can drive UI overlays / logs).
3. **Indicator triggers.** Cross-interval condition trees are
   evaluated by reusing :func:`scanner.engine.evaluate_group` against
   a :class:`EvaluationContext` built from the
   :class:`BarsRegistry` view for ``(symbol, trigger.interval or
   default_interval)``.
4. **OCO.** ``cancel_on="any_fire"`` cancels siblings inline at fire
   time; ``cancel_on="full_closeout"`` (default, bracket-friendly)
   defers the sibling cancel until ``Position.qty_open == 0`` (signal
   arrives via :meth:`PositionTracker.subscribe`).
5. **EOD kill switch.** When the bar's wall-clock time crosses
   ``session_close - eod_offset_min``, we (a) disarm every trigger,
   (b) cancel every in-flight order, (c) submit a market exit for
   the entire ``qty_open``. Audited as ``eod_kill_switch_fired``.
6. **Panic flatten — phase 1.** :meth:`panic_flatten_position`
   synchronously disarms every trigger and cancels every in-flight
   order. Phase 2 (the actual market exit submission with progress
   dialog) is the GUI's responsibility — but we expose
   :meth:`submit_market_flatten` as a primitive it can call.

Threading
---------

Every public mutator is decorated with ``@require_tk_thread``. The
evaluator subscribes to :class:`PositionTracker` event delivery,
which is also Tk-thread-only (re-entrancy safe via the tracker's
own queue). Read-only queries (``stats``, ``attached_strategy``)
have no thread restriction.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from datetime import time as dtime
from typing import Any

from ..core.thread_guard import require_tk_thread
from ..positions.model import Position, PositionEvent, PositionEventKind
from ..positions.tracker import PositionTracker
from ..scanner.engine import EvaluationContext as _ScannerEvaluationContext
from ..scanner.engine import make_context
from .audit import AuditLog
from .dispatch import ExitTriggerContext, check_trigger_decision
from .model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    OCOGroup,
    OrderSide,
    TriggerKind,
)
from .signals import ExitOrderKind, ExitSignal, ExitSignalSink
from .spec import (
    Bar,
    Decision,
    TriggerState,
    compute_qty_at_fire,
)

LOG = logging.getLogger(__name__)


__all__ = [
    "ExitEvaluator",
    "AttachmentNotFound",
    "EvaluatorStats",
]


# ---------------------------------------------------------------------------
# Internal slot dataclasses
# ---------------------------------------------------------------------------


@dataclass
class _TriggerSlot:
    """Per-trigger runtime state owned by the evaluator."""

    trigger: ExitTrigger
    armed: bool = True
    state: TriggerState = field(default_factory=TriggerState)
    submitted_order_ids: list[str] = field(default_factory=list)
    last_fire_bar_ts_ns: int | None = None
    error_count: int = 0
    broken: bool = False


@dataclass
class _LegSlot:
    leg: ExitLeg
    triggers: list[_TriggerSlot]


@dataclass
class _Attachment:
    strategy: ExitStrategy
    legs: dict[str, _LegSlot]
    position_id: str
    oco_lookup: dict[str, OCOGroup]
    pending_full_closeout_cancel: set[str] = field(default_factory=set)
    eod_fired: bool = False


@dataclass
class EvaluatorStats:
    fires: int = 0
    cancels: int = 0
    eod_fires: int = 0
    errors: int = 0
    indicator_evaluations: int = 0
    bars_processed: int = 0


class AttachmentNotFound(LookupError):
    """Raised when an operation targets a position with no attached strategy."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bar_ts_ns(bar: Bar) -> int | None:
    if bar.date is None:
        return None
    try:
        return int(bar.date.timestamp() * 1_000_000_000)
    except (OSError, ValueError):  # pragma: no cover - extreme dates
        return None


def _no_fire(reason: str = "") -> Decision:
    return Decision(fire=False, reason=reason)


def _order_kind_for_decision(trigger_kind: TriggerKind, decision: Decision) -> ExitOrderKind:
    """Map a trigger.kind + Decision to the resulting order kind on the sink.

    Trailing-stop / TOD / indicator collapse to MARKET once they fire
    (the evaluator owns the state machine; the sink only sees the
    resulting order). Native limit/stop/stop_limit pass through with
    their own kind.
    """
    if trigger_kind == TriggerKind.LIMIT:
        return ExitOrderKind.LIMIT
    if trigger_kind == TriggerKind.STOP:
        return ExitOrderKind.STOP
    if trigger_kind == TriggerKind.STOP_LIMIT:
        return ExitOrderKind.STOP_LIMIT
    return ExitOrderKind.MARKET


# ---------------------------------------------------------------------------
# ExitEvaluator
# ---------------------------------------------------------------------------


class ExitEvaluator:
    """Live exit-strategy evaluator.

    Construct once per :class:`PositionTracker`; it self-subscribes to
    tracker events to drive OCO ``full_closeout`` cleanup and to
    auto-detach when a position fully closes.
    """

    def __init__(
        self,
        *,
        tracker: PositionTracker,
        sink: ExitSignalSink,
        audit: AuditLog | None = None,
        bars_registry: Any | None = None,
        session_close_time: dtime = dtime(16, 0),
        clock: Callable[[], datetime] = _utc_now,
        default_interval: str = "1m",
    ) -> None:
        self._tracker = tracker
        self._sink = sink
        self._audit = audit
        self._bars_registry = bars_registry
        self._session_close_time = session_close_time
        self._clock = clock
        self._default_interval = default_interval
        self._attached: dict[str, _Attachment] = {}
        self._stats = EvaluatorStats()
        self._unsubscribe_tracker = tracker.subscribe(self._on_position_event)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Unsubscribe from the tracker. Idempotent."""
        if self._unsubscribe_tracker is not None:
            try:
                self._unsubscribe_tracker()
            except Exception:  # pragma: no cover - tracker bug
                pass
            self._unsubscribe_tracker = None

    # ------------------------------------------------------------------
    # Read-only queries (any thread)
    # ------------------------------------------------------------------

    def attached_strategy(self, position_id: str) -> ExitStrategy | None:
        att = self._attached.get(position_id)
        return att.strategy if att else None

    def is_attached(self, position_id: str) -> bool:
        return position_id in self._attached

    def attached_position_ids(self) -> list[str]:
        return list(self._attached.keys())

    def stats(self) -> EvaluatorStats:
        return EvaluatorStats(
            fires=self._stats.fires,
            cancels=self._stats.cancels,
            eod_fires=self._stats.eod_fires,
            errors=self._stats.errors,
            indicator_evaluations=self._stats.indicator_evaluations,
            bars_processed=self._stats.bars_processed,
        )

    def trigger_state(
        self, position_id: str, leg_id: str, trigger_id: str
    ) -> _TriggerSlot | None:
        att = self._attached.get(position_id)
        if att is None:
            return None
        leg_slot = att.legs.get(leg_id)
        if leg_slot is None:
            return None
        for tslot in leg_slot.triggers:
            if tslot.trigger.id == trigger_id:
                return tslot
        return None

    # ------------------------------------------------------------------
    # Attach / detach
    # ------------------------------------------------------------------

    @require_tk_thread
    def attach_strategy(self, position_id: str, strategy: ExitStrategy) -> None:
        """Bind a strategy instance to a position.

        Replaces any previously-attached strategy (auto-detaches first
        with reason ``auto-replace``). The provided strategy is held
        by reference; the caller is responsible for not mutating it
        after attach (per the v1 "frozen at attach time" rule).

        Raises :class:`KeyError` if the position is unknown.
        """
        pos = self._tracker.get(position_id)
        if pos is None:
            raise KeyError(f"unknown position {position_id!r}")
        if not pos.is_open:
            raise ValueError(
                f"position {position_id!r} is not open; refusing attach"
            )

        if position_id in self._attached:
            self._do_detach(position_id, reason="auto-replace", cancel_in_flight=True)

        leg_slots: dict[str, _LegSlot] = {}
        for leg in strategy.legs:
            triggers = [
                _TriggerSlot(trigger=t) for t in leg.triggers if t.enabled
            ]
            leg_slots[leg.id] = _LegSlot(leg=leg, triggers=triggers)

        oco_lookup: dict[str, OCOGroup] = {}
        for grp in strategy.oco_groups:
            for lid in grp.leg_ids:
                oco_lookup[lid] = grp

        self._attached[position_id] = _Attachment(
            strategy=strategy,
            legs=leg_slots,
            position_id=position_id,
            oco_lookup=oco_lookup,
        )

        # Update tracker's strategy_id for visibility (does NOT make
        # the tracker the source-of-truth; we own the live state).
        try:
            self._tracker.bind_strategy(position_id, strategy.id)
        except Exception:  # pragma: no cover - already bound
            LOG.exception("ExitEvaluator: tracker.bind_strategy raised")

        if self._audit is not None:
            self._audit.append(
                "strategy_attach",
                strategy_id=strategy.id,
                position_id=position_id,
                meta={"leg_count": len(strategy.legs)},
            )

    @require_tk_thread
    def detach_strategy(
        self,
        position_id: str,
        *,
        reason: str = "manual",
        cancel_in_flight: bool = True,
    ) -> bool:
        """Detach the strategy from a position.

        Returns ``True`` if a strategy was detached, ``False`` if no
        strategy was attached (idempotent). Disarms every trigger and
        — if ``cancel_in_flight`` — cancels every in-flight order via
        the sink.
        """
        return self._do_detach(
            position_id, reason=reason, cancel_in_flight=cancel_in_flight
        )

    def _do_detach(
        self,
        position_id: str,
        *,
        reason: str,
        cancel_in_flight: bool,
    ) -> bool:
        att = self._attached.pop(position_id, None)
        if att is None:
            return False

        for leg_slot in att.legs.values():
            for tslot in leg_slot.triggers:
                tslot.armed = False

        cancelled = 0
        if cancel_in_flight:
            try:
                cancelled = self._sink.cancel_all_for_position(position_id)
            except Exception:
                LOG.exception("ExitEvaluator: sink.cancel_all_for_position raised")
        self._stats.cancels += cancelled

        # Tracker-side detach (non-fatal if already unbound).
        pos = self._tracker.get(position_id)
        if pos is not None and pos.strategy_id is not None:
            try:
                self._tracker.unbind_strategy(position_id, reason=reason)
            except Exception:
                LOG.debug("ExitEvaluator: tracker.unbind_strategy raised", exc_info=True)

        if self._audit is not None:
            self._audit.append(
                "strategy_detach",
                strategy_id=att.strategy.id,
                position_id=position_id,
                meta={"reason": reason, "cancelled_in_flight": cancelled},
            )
        return True

    # ------------------------------------------------------------------
    # Per-bar evaluation
    # ------------------------------------------------------------------

    @require_tk_thread
    def on_bar(
        self,
        position_id: str,
        bar: Bar,
        *,
        is_close: bool = True,
        interval: str | None = None,
    ) -> list[ExitSignal]:
        """Evaluate every armed trigger for the bound strategy.

        Returns the list of fired :class:`ExitSignal` instances. Each
        has already been submitted to the sink. ``interval`` (default
        the evaluator's ``default_interval``) controls which interval
        is treated as "this bar" for cross-interval indicator triggers.
        """
        att = self._attached.get(position_id)
        if att is None:
            return []
        pos = self._tracker.get(position_id)
        if pos is None or not pos.is_open:
            return []

        self._stats.bars_processed += 1

        # 1) EOD kill switch — checked first so it pre-empts any
        # other trigger work this bar.
        if (
            att.strategy.eod_kill_switch
            and not att.eod_fired
            and bar.date is not None
            and self._eod_threshold_reached(att.strategy, bar.date)
        ):
            sig = self._fire_eod_kill(att, pos, bar)
            return [sig] if sig is not None else []

        fired: list[ExitSignal] = []
        # Iterate a list copy so OCO sibling-cancel mutating armed flags
        # mid-loop is harmless.
        leg_items = list(att.legs.items())
        for leg_id, leg_slot in leg_items:
            if not leg_slot.leg.enabled:
                continue
            for tslot in list(leg_slot.triggers):
                if not tslot.armed or tslot.broken:
                    continue
                sig = self._evaluate_one_trigger(
                    att, pos, leg_slot.leg, tslot, bar, is_close=is_close
                )
                if sig is None:
                    continue
                fired.append(sig)
                self._handle_oco_after_fire(att, leg_id)
        return fired

    # ------------------------------------------------------------------
    # Trigger evaluation
    # ------------------------------------------------------------------

    def _evaluate_one_trigger(
        self,
        att: _Attachment,
        pos: Position,
        leg: ExitLeg,
        tslot: _TriggerSlot,
        bar: Bar,
        *,
        is_close: bool,
    ) -> ExitSignal | None:
        trigger = tslot.trigger
        kind = trigger.kind

        try:
            scanner_eval_ctx = None
            if kind == TriggerKind.INDICATOR and (is_close or trigger.evaluate_intrabar):
                scanner_eval_ctx = self._build_indicator_context(trigger, pos)
            decision = check_trigger_decision(
                trigger,
                ExitTriggerContext(
                    position=pos,
                    bar=bar,
                    is_close=is_close,
                    trigger_state=tslot.state,
                    now=bar.date or self._clock(),
                    scanner_eval_ctx=scanner_eval_ctx,
                )
            )
        except Exception as exc:
            tslot.broken = True
            tslot.error_count += 1
            self._stats.errors += 1
            LOG.exception(
                "ExitEvaluator: evaluator raised on trigger %s; marking broken",
                trigger.id,
            )
            if self._audit is not None:
                self._audit.append(
                    "cancel",
                    strategy_id=att.strategy.id,
                    position_id=pos.id,
                    leg_id=leg.id,
                    trigger_id=trigger.id,
                    meta={"error": repr(exc)},
                )
            return None

        if not decision.fire:
            return None

        # Dedup: don't fire the same trigger twice within the same bar.
        ts_ns = _bar_ts_ns(bar)
        if (
            ts_ns is not None
            and tslot.last_fire_bar_ts_ns is not None
            and tslot.last_fire_bar_ts_ns == ts_ns
        ):
            return None
        tslot.last_fire_bar_ts_ns = ts_ns
        tslot.state.fire_count += 1

        # Resolve qty against current qty_open (B6).
        qty_resolved = compute_qty_at_fire(trigger, pos)
        if qty_resolved <= 0:
            return None

        order_kind = _order_kind_for_decision(kind, decision)
        side = OrderSide.SELL if pos.side == "long" else OrderSide.BUY
        price_for_signal: float | None
        limit_price_for_signal: float | None
        if order_kind == ExitOrderKind.MARKET:
            price_for_signal = None
            limit_price_for_signal = None
        elif order_kind == ExitOrderKind.STOP_LIMIT:
            price_for_signal = decision.fire_price
            limit_price_for_signal = decision.limit_price
        else:
            price_for_signal = decision.fire_price
            limit_price_for_signal = None

        signal = ExitSignal.new(
            strategy_id=att.strategy.id,
            position_id=pos.id,
            leg_id=leg.id,
            trigger_id=trigger.id,
            kind=order_kind,
            side=side,
            qty=qty_resolved,
            price=price_for_signal,
            limit_price=limit_price_for_signal,
            label=trigger.label or leg.label,
        )

        if self._audit is not None:
            fire_meta: dict[str, Any] = {"reason": decision.reason, "kind": kind.value}
            if decision.evidence:
                fire_meta["evidence"] = [
                    {
                        "node_id": ev.node_id,
                        "bars_ago": int(ev.bars_ago),
                        "timestamp": ev.timestamp,
                        "value": ev.value,
                    }
                    for ev in decision.evidence
                ]
            # Chandelier-specific: surface realized gap slippage so the
            # journal teaches the user how often their stops would have
            # been filled worse than the level on a gap.
            if kind == TriggerKind.CHANDELIER:
                slip = float(getattr(tslot.state, "chandelier_realized_slippage", 0.0))
                if slip > 0:
                    fire_meta["realized_slippage"] = slip
            self._audit.append(
                "fire",
                strategy_id=att.strategy.id,
                position_id=pos.id,
                leg_id=leg.id,
                trigger_id=trigger.id,
                qty=qty_resolved,
                price=decision.fire_price if decision.fire_price else None,
                meta=fire_meta,
            )

        try:
            order_id = self._sink.submit(signal)
        except Exception as exc:
            tslot.broken = True
            tslot.error_count += 1
            self._stats.errors += 1
            LOG.exception(
                "ExitEvaluator: sink.submit raised for trigger %s; marking broken",
                trigger.id,
            )
            if self._audit is not None:
                self._audit.append(
                    "cancel",
                    strategy_id=att.strategy.id,
                    position_id=pos.id,
                    leg_id=leg.id,
                    trigger_id=trigger.id,
                    meta={"error": repr(exc), "stage": "submit"},
                )
            return None

        tslot.submitted_order_ids.append(order_id)
        # Trigger has fired; disarm so it doesn't re-fire on the next
        # bar before the order has filled. Re-arming is the user's
        # explicit action via re-attach.
        tslot.armed = False
        self._stats.fires += 1

        if self._audit is not None:
            self._audit.append(
                "submit",
                strategy_id=att.strategy.id,
                position_id=pos.id,
                leg_id=leg.id,
                trigger_id=trigger.id,
                meta={"order_id": order_id, "kind": order_kind.value},
            )
        return signal

    def _build_indicator_context(
        self,
        trigger: ExitTrigger,
        pos: Position,
    ) -> _ScannerEvaluationContext | None:
        """Build the scanner context consumed by the dispatch INDICATOR handler."""
        if self._bars_registry is None:
            return None
        if trigger.condition is None:
            return None

        interval = trigger.interval or self._default_interval
        view = self._bars_registry.get_view(pos.symbol, interval)
        if view is None:
            return None

        candles = view.memo.candles
        if not candles:
            return None

        try:
            ctx = make_context(
                symbol=pos.symbol,
                interval=interval,
                candles=candles,
                memo=view.memo,
                bars=view.bars,
                bars_registry=self._bars_registry,
            )
            self._stats.indicator_evaluations += 1
            return ctx
        except Exception:  # noqa: BLE001
            LOG.exception(
                "ExitEvaluator: failed to build indicator context for trigger %s",
                trigger.id,
            )
            return None

    # ------------------------------------------------------------------
    # OCO machinery
    # ------------------------------------------------------------------

    def _handle_oco_after_fire(self, att: _Attachment, fired_leg_id: str) -> None:
        grp = att.oco_lookup.get(fired_leg_id)
        if grp is None:
            return
        if grp.cancel_on == "any_fire":
            self._cancel_sibling_legs(att, fired_leg_id=fired_leg_id, group=grp)
        elif grp.cancel_on == "full_closeout":
            for sib_id in grp.leg_ids:
                if sib_id != fired_leg_id:
                    att.pending_full_closeout_cancel.add(sib_id)

    def _cancel_sibling_legs(
        self, att: _Attachment, *, fired_leg_id: str, group: OCOGroup
    ) -> None:
        for sib_id in group.leg_ids:
            if sib_id == fired_leg_id:
                continue
            self._cancel_leg(att, sib_id, reason="oco_cancel")

    def _cancel_leg(
        self, att: _Attachment, leg_id: str, *, reason: str
    ) -> None:
        leg_slot = att.legs.get(leg_id)
        if leg_slot is None:
            return
        for tslot in leg_slot.triggers:
            tslot.armed = False
            for order_id in list(tslot.submitted_order_ids):
                try:
                    self._sink.cancel(order_id)
                except Exception:
                    LOG.exception(
                        "ExitEvaluator: sink.cancel raised for order %s",
                        order_id,
                    )
                self._stats.cancels += 1
            tslot.submitted_order_ids.clear()
        if self._audit is not None:
            self._audit.append(
                "cancel",
                strategy_id=att.strategy.id,
                position_id=att.position_id,
                leg_id=leg_id,
                meta={"reason": reason},
            )

    # ------------------------------------------------------------------
    # EOD kill switch
    # ------------------------------------------------------------------

    def _eod_threshold_reached(
        self, strategy: ExitStrategy, bar_dt: datetime
    ) -> bool:
        """Return True iff ``bar_dt``'s wall-clock is at/after the EOD threshold.

        Threshold = ``session_close_time - eod_offset_min`` (in the
        bar's tz). For naive datetimes, treats them as already in the
        right timezone — the GUI passes localised bar timestamps.
        """
        bar_local = bar_dt
        sess_dt = bar_local.replace(
            hour=self._session_close_time.hour,
            minute=self._session_close_time.minute,
            second=0,
            microsecond=0,
        )
        from datetime import timedelta

        threshold = sess_dt - timedelta(minutes=strategy.eod_offset_min)
        return bar_local >= threshold

    def _fire_eod_kill(
        self, att: _Attachment, pos: Position, bar: Bar
    ) -> ExitSignal | None:
        """Cancel everything + submit a market exit for the entire qty_open."""
        # Cancel all in-flight orders for this position.
        try:
            cancelled = self._sink.cancel_all_for_position(pos.id)
        except Exception:
            LOG.exception("ExitEvaluator: sink.cancel_all_for_position raised in EOD")
            cancelled = 0
        self._stats.cancels += cancelled

        # Disarm everything.
        for leg_slot in att.legs.values():
            for tslot in leg_slot.triggers:
                tslot.armed = False
                tslot.submitted_order_ids.clear()

        att.eod_fired = True

        if pos.qty_open <= 0:
            return None

        side = OrderSide.SELL if pos.side == "long" else OrderSide.BUY
        signal = ExitSignal.new(
            strategy_id=att.strategy.id,
            position_id=pos.id,
            leg_id="__eod__",
            trigger_id="__eod__",
            kind=ExitOrderKind.MARKET,
            side=side,
            qty=pos.qty_open,
            label="EOD kill switch",
        )

        if self._audit is not None:
            self._audit.append(
                "eod_kill_switch_fired",
                strategy_id=att.strategy.id,
                position_id=pos.id,
                qty=pos.qty_open,
                price=bar.close,
                meta={"cancelled_orders": cancelled},
            )

        try:
            order_id = self._sink.submit(signal)
        except Exception:
            LOG.exception(
                "ExitEvaluator: sink.submit raised on EOD kill; logged but propagated"
            )
            self._stats.errors += 1
            return None

        self._stats.eod_fires += 1
        if self._audit is not None:
            self._audit.append(
                "submit",
                strategy_id=att.strategy.id,
                position_id=pos.id,
                meta={"order_id": order_id, "kind": "market", "tag": "eod"},
            )
        return signal

    # ------------------------------------------------------------------
    # Panic flatten — phase 1 (synchronous)
    # ------------------------------------------------------------------

    @require_tk_thread
    def panic_flatten_position(self, position_id: str) -> int:
        """Phase 1 of panic flatten: disarm + cancel-all.

        Phase 2 — submitting market exits with a progress dialog — is
        the GUI's responsibility (it calls
        :meth:`submit_market_flatten` per position with progress
        callbacks). Returns the number of orders cancelled.
        """
        att = self._attached.get(position_id)
        if att is None:
            return 0
        for leg_slot in att.legs.values():
            for tslot in leg_slot.triggers:
                tslot.armed = False
                tslot.submitted_order_ids.clear()
        try:
            n = self._sink.cancel_all_for_position(position_id)
        except Exception:
            LOG.exception("ExitEvaluator: sink.cancel_all raised in panic flatten")
            n = 0
        self._stats.cancels += n
        if self._audit is not None:
            self._audit.append(
                "panic_flatten",
                strategy_id=att.strategy.id,
                position_id=position_id,
                meta={"phase": 1, "cancelled_orders": n},
            )
        return n

    @require_tk_thread
    def submit_market_flatten(self, position_id: str) -> ExitSignal | None:
        """Phase 2 of panic flatten: market exit for ``qty_open``.

        Called by the GUI after :meth:`panic_flatten_position`. Safe
        to call multiple times — each call submits a fresh market
        signal for the *current* ``qty_open``, so partial fills can
        be re-submitted for the residual.
        """
        att = self._attached.get(position_id)
        pos = self._tracker.get(position_id)
        if pos is None or not pos.is_open:
            return None
        side = OrderSide.SELL if pos.side == "long" else OrderSide.BUY
        strat_id = att.strategy.id if att is not None else "__manual__"
        signal = ExitSignal.new(
            strategy_id=strat_id,
            position_id=position_id,
            leg_id="__panic__",
            trigger_id="__panic__",
            kind=ExitOrderKind.MARKET,
            side=side,
            qty=pos.qty_open,
            label="Panic flatten",
        )
        try:
            order_id = self._sink.submit(signal)
        except Exception:
            LOG.exception("ExitEvaluator: sink.submit raised on panic flatten phase 2")
            self._stats.errors += 1
            return None
        if self._audit is not None:
            self._audit.append(
                "panic_flatten",
                strategy_id=strat_id,
                position_id=position_id,
                qty=pos.qty_open,
                meta={"phase": 2, "order_id": order_id},
            )
        return signal

    # ------------------------------------------------------------------
    # Tracker subscription — drives full_closeout OCO + auto-detach
    # ------------------------------------------------------------------

    def _on_position_event(self, ev: PositionEvent, pos: Position) -> None:
        """Subscriber callback from :class:`PositionTracker`.

        Handles two responsibilities:

        1. ``full_closeout`` OCO: when ``Position.qty_open == 0``,
           any leg ids in the pending-cancel set are cancelled now.
        2. Auto-detach on full close (the tracker has already
           cleared ``Position.strategy_id``; we drop our internal
           attachment record + audit it).
        """
        att = self._attached.get(ev.position_id)
        if att is None:
            return

        if ev.kind in (PositionEventKind.PARTIAL_CLOSE, PositionEventKind.CLOSE):
            if pos.qty_open <= 0:
                # Drain pending full_closeout cancels first.
                for leg_id in list(att.pending_full_closeout_cancel):
                    self._cancel_leg(att, leg_id, reason="oco_full_closeout")
                att.pending_full_closeout_cancel.clear()
                # Now auto-detach.
                self._do_detach(
                    ev.position_id,
                    reason="position_closed",
                    cancel_in_flight=True,
                )
        elif ev.kind == PositionEventKind.STRATEGY_UNBIND:
            # Tracker cleared the strategy_id (e.g. via its own
            # remove flow). Drop our attachment to stay consistent.
            self._attached.pop(ev.position_id, None)
