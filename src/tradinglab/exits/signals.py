"""Exit signal protocol + concrete sinks.

The **evaluator** layer (``exits.evaluator``) decides, for a given
trigger fire, the resolved order kind / qty / prices and emits an
:class:`ExitSignal`. The signal is then handed to a **sink** that
delivers the order somewhere — paper engine, manual paper notification,
or eventually a real broker. The sink layer keeps the evaluator
broker-agnostic.

Three concrete sinks ship in v1:

* :class:`PaperBrokerSink` — translates :class:`ExitSignal` →
  ``PaperOrder`` and delegates to a :class:`PaperBrokerEngine`. This
  is the auto-fill paper trading mode.
* :class:`ManualPaperSink` — does **not** fill; instead it audits the
  signal and notifies an external listener (the GUI's "PAPER (MANUAL)"
  notifier) so the user can mirror the exit on a real broker. Returns
  a synthetic id and tracks it as "working" until the user marks it
  closed via the manual sink API. This sink is Tk-free; the GUI
  subscribes to :meth:`ManualPaperSink.subscribe`.
* :class:`SchwabTraderSink` — explicit not-yet-implemented sentinel
  used for menu wiring; raises :class:`NotImplementedError` on submit.

All sinks satisfy the :class:`ExitSignalSink` Protocol.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Protocol,
)

from ..core.thread_guard import require_tk_thread
from .model import OrderSide

if TYPE_CHECKING:  # pragma: no cover - import-cycle guard
    from .audit import AuditLog
    from .paper_engine import PaperBrokerEngine

LOG = logging.getLogger(__name__)

__all__ = [
    "ExitOrderKind",
    "ExitSignal",
    "ExitSignalSink",
    "PaperBrokerSink",
    "ManualPaperSink",
    "ManualSignalEvent",
    "SchwabTraderSink",
    "SchwabTraderNotConfigured",
]


class ExitOrderKind(str, Enum):
    """Order kinds the evaluator may emit.

    Trailing-stop / time-of-day / indicator triggers all materialise
    into :data:`MARKET` once they fire — the evaluator owns their
    state machinery; the sink only sees the resulting market exit.
    """

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@dataclass(frozen=True)
class ExitSignal:
    """A fully-resolved exit instruction.

    Produced by :class:`exits.evaluator.ExitEvaluator` at trigger fire
    time. The evaluator is responsible for resolving:

    * ``qty`` against ``position.qty_open`` *now* (B6 fire-time qty
      resolution).
    * ``price`` / ``limit_price`` for native orders.
    * ``side`` from the position direction.

    The signal is opaque to the sink — the sink translates it to its
    own representation (PaperOrder, broker REST payload, GUI banner).
    """

    id: str
    strategy_id: str
    position_id: str
    leg_id: str
    trigger_id: str
    kind: ExitOrderKind
    side: OrderSide
    qty: float
    price: float | None = None
    limit_price: float | None = None
    label: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, **kwargs: Any) -> ExitSignal:
        """Construct with an auto-assigned id."""
        return cls(id=uuid.uuid4().hex, **kwargs)


class ExitSignalSink(Protocol):
    """Protocol every concrete sink satisfies.

    Sinks return broker-side order ids on :meth:`submit`; the evaluator
    associates these ids with the originating ``leg_id`` so a later
    OCO cancel can call :meth:`cancel` on each.

    All methods are required to be Tk-thread safe (they run from the
    evaluator which lives on the Tk thread). Concrete sinks enforce
    this with ``@require_tk_thread`` on every public method.
    """

    def submit(self, signal: ExitSignal) -> str: ...

    def cancel(self, order_id: str) -> bool: ...

    def cancel_all_for_position(self, position_id: str) -> int: ...

    def working_order_ids_for_position(self, position_id: str) -> list[str]: ...


# ---------------------------------------------------------------------------
# PaperBrokerSink — auto-fill paper trading
# ---------------------------------------------------------------------------


class PaperBrokerSink:
    """Translate :class:`ExitSignal` → ``PaperOrder`` and submit.

    Holds a mapping ``signal_id → paper_order_id`` so callers can cancel
    by either id. The sink does NOT own a position tracker; the engine
    does. The sink is a pure translator + lookup layer.
    """

    def __init__(self, engine: PaperBrokerEngine) -> None:
        self._engine = engine
        # Forward map: ExitSignal.id -> paper order id
        self._signal_to_order: dict[str, str] = {}
        # Reverse map: paper order id -> ExitSignal.id (for working_order_ids)
        self._order_to_signal: dict[str, str] = {}
        # Per-position working signal ids — kept here (not just on the
        # engine) so the sink can answer working_order_ids_for_position
        # even after the engine has filled/cancelled, where the engine's
        # internal mapping would have removed the entry.
        self._working_by_position: dict[str, list[str]] = {}

    @require_tk_thread
    def submit(self, signal: ExitSignal) -> str:
        from .paper_engine import PaperOrder, PaperOrderKind  # local import — cycle guard

        kind_map = {
            ExitOrderKind.MARKET: PaperOrderKind.MARKET,
            ExitOrderKind.LIMIT: PaperOrderKind.LIMIT,
            ExitOrderKind.STOP: PaperOrderKind.STOP,
            ExitOrderKind.STOP_LIMIT: PaperOrderKind.STOP_LIMIT,
        }
        order = PaperOrder(
            id=uuid.uuid4().hex,
            position_id=signal.position_id,
            kind=kind_map[signal.kind],
            side=signal.side,
            qty=signal.qty,
            price=signal.price,
            limit_price=signal.limit_price,
            label=signal.label,
            extra=dict(signal.extra),
        )
        order_id = self._engine.submit(order)
        self._signal_to_order[signal.id] = order_id
        self._order_to_signal[order_id] = signal.id
        self._working_by_position.setdefault(signal.position_id, []).append(order_id)
        return order_id

    @require_tk_thread
    def cancel(self, order_id: str) -> bool:
        cancelled = self._engine.cancel(order_id)
        if cancelled:
            self._forget_order(order_id)
        return cancelled

    @require_tk_thread
    def cancel_all_for_position(self, position_id: str) -> int:
        # Snapshot our own per-position list before delegating so we can
        # purge the forward/reverse maps cleanly regardless of how the
        # engine accounted for each id.
        order_ids = list(self._working_by_position.get(position_id, []))
        n = self._engine.cancel_all_for_position(position_id)
        for oid in order_ids:
            self._forget_order(oid)
        # Defensive: drop any stale per-position entry that survived
        # (e.g. an id was double-tracked due to a prior bookkeeping bug).
        self._working_by_position.pop(position_id, None)
        return n

    def working_order_ids_for_position(self, position_id: str) -> list[str]:
        """Snapshot of working paper order ids for a given position.

        Read-only — safe from any thread.
        """
        return list(self._working_by_position.get(position_id, []))

    def _forget_order(self, order_id: str) -> None:
        sid = self._order_to_signal.pop(order_id, None)
        if sid is not None:
            self._signal_to_order.pop(sid, None)
        for pos_id, ids in list(self._working_by_position.items()):
            if order_id in ids:
                ids.remove(order_id)
                if not ids:
                    del self._working_by_position[pos_id]
                break


# ---------------------------------------------------------------------------
# ManualPaperSink — surface signal to user, do not auto-fill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManualSignalEvent:
    """Event published by :class:`ManualPaperSink` to its subscribers.

    The GUI uses these to drive the orange "PAPER (MANUAL)" notification
    surface. Tests assert the events without needing Tk.
    """

    kind: str  # "submitted", "cancelled", "ack-fill"
    signal: ExitSignal | None
    order_id: str


class ManualPaperSink:
    """Sink that mirrors signals to a subscriber callback instead of filling.

    For users who paper-trade by manually executing on a real broker.
    Maintains a "working" set keyed by synthetic ``manual-<uuid>`` ids;
    the user (via the GUI) may call :meth:`acknowledge_fill` to clear an
    id, or :meth:`cancel` to drop it without a fill.

    The sink is Tk-free; the GUI subscribes via :meth:`subscribe` and
    is responsible for marshalling onto the Tk thread before drawing.
    """

    def __init__(self, *, audit: AuditLog | None = None) -> None:
        self._audit = audit
        self._working_by_position: dict[str, list[str]] = {}
        self._signals_by_id: dict[str, ExitSignal] = {}
        self._subscribers: list[Callable[[ManualSignalEvent], None]] = []
        self._lock = threading.Lock()

    def subscribe(
        self, callback: Callable[[ManualSignalEvent], None]
    ) -> Callable[[], None]:
        """Register ``callback``; returns an unsubscribe handle."""
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _unsubscribe

    def _emit(self, event: ManualSignalEvent) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(event)
            except Exception:  # pragma: no cover - subscriber bug
                LOG.exception("ManualPaperSink subscriber raised; continuing")

    @require_tk_thread
    def submit(self, signal: ExitSignal) -> str:
        order_id = f"manual-{uuid.uuid4().hex}"
        self._signals_by_id[order_id] = signal
        self._working_by_position.setdefault(signal.position_id, []).append(order_id)
        if self._audit is not None:
            self._audit.append(
                "submit",
                strategy_id=signal.strategy_id,
                position_id=signal.position_id,
                leg_id=signal.leg_id,
                trigger_id=signal.trigger_id,
                qty=signal.qty,
                price=signal.price,
                meta={"sink": "manual", "order_id": order_id, "label": signal.label},
            )
        self._emit(ManualSignalEvent(kind="submitted", signal=signal, order_id=order_id))
        return order_id

    @require_tk_thread
    def cancel(self, order_id: str) -> bool:
        if order_id not in self._signals_by_id:
            return False
        signal = self._signals_by_id.pop(order_id)
        ids = self._working_by_position.get(signal.position_id, [])
        if order_id in ids:
            ids.remove(order_id)
            if not ids:
                self._working_by_position.pop(signal.position_id, None)
        if self._audit is not None:
            self._audit.append(
                "cancel",
                strategy_id=signal.strategy_id,
                position_id=signal.position_id,
                leg_id=signal.leg_id,
                trigger_id=signal.trigger_id,
                meta={"sink": "manual", "order_id": order_id},
            )
        self._emit(ManualSignalEvent(kind="cancelled", signal=signal, order_id=order_id))
        return True

    @require_tk_thread
    def cancel_all_for_position(self, position_id: str) -> int:
        ids = list(self._working_by_position.get(position_id, []))
        n = 0
        for oid in ids:
            if self.cancel(oid):
                n += 1
        return n

    @require_tk_thread
    def acknowledge_fill(self, order_id: str) -> bool:
        """User has executed this exit on a real broker; clear it."""
        if order_id not in self._signals_by_id:
            return False
        signal = self._signals_by_id.pop(order_id)
        ids = self._working_by_position.get(signal.position_id, [])
        if order_id in ids:
            ids.remove(order_id)
            if not ids:
                self._working_by_position.pop(signal.position_id, None)
        if self._audit is not None:
            self._audit.append(
                "fill",
                strategy_id=signal.strategy_id,
                position_id=signal.position_id,
                leg_id=signal.leg_id,
                trigger_id=signal.trigger_id,
                qty=signal.qty,
                price=signal.price,
                meta={"sink": "manual", "order_id": order_id, "ack": True},
            )
        self._emit(ManualSignalEvent(kind="ack-fill", signal=signal, order_id=order_id))
        return True

    def working_order_ids_for_position(self, position_id: str) -> list[str]:
        return list(self._working_by_position.get(position_id, []))


# ---------------------------------------------------------------------------
# SchwabTraderSink — explicit stub
# ---------------------------------------------------------------------------


class SchwabTraderNotConfigured(RuntimeError):
    """Raised when the Schwab live-trading sink is invoked before wiring."""


class SchwabTraderSink:
    """Stub sink for the future Schwab Trader API integration.

    Emits a clear, audited error on submit so menu wiring can surface
    the sink as a selectable option without silently dropping signals.
    Wiring will replace this class once the broker integration is
    implemented.
    """

    def __init__(self, *, audit: AuditLog | None = None) -> None:
        self._audit = audit

    @require_tk_thread
    def submit(self, signal: ExitSignal) -> str:
        if self._audit is not None:
            self._audit.append(
                "submit",
                strategy_id=signal.strategy_id,
                position_id=signal.position_id,
                leg_id=signal.leg_id,
                trigger_id=signal.trigger_id,
                meta={"sink": "schwab", "status": "not_configured"},
            )
        raise SchwabTraderNotConfigured(
            "SchwabTraderSink: live broker integration not yet implemented; "
            "switch the strategy's sink to PaperBrokerSink or ManualPaperSink."
        )

    @require_tk_thread
    def cancel(self, order_id: str) -> bool:
        raise SchwabTraderNotConfigured("SchwabTraderSink: cancel not implemented.")

    @require_tk_thread
    def cancel_all_for_position(self, position_id: str) -> int:
        raise SchwabTraderNotConfigured(
            "SchwabTraderSink: cancel_all_for_position not implemented."
        )

    def working_order_ids_for_position(self, position_id: str) -> list[str]:
        return []
