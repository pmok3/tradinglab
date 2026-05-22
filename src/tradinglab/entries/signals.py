"""Entry signal protocol + concrete sinks.

Mirror of :mod:`tradinglab.exits.signals` for the entry side of the
order lifecycle. The :class:`entries.evaluator.EntryEvaluator` resolves
trigger fires into :class:`EntrySignal` instructions; concrete sinks
deliver them somewhere (paper engine, manual notifier, broker REST).

Two concrete sinks ship in v1:

* :class:`EntryPaperSink` — translates :class:`EntrySignal` →
  ``PaperOrder`` with ``target_kind=PENDING_ENTRY`` and delegates to a
  :class:`PaperBrokerEngine`. This is the auto-fill paper trading mode
  for entries. On fill the engine routes through
  :meth:`PaperBrokerEngine.on_bar_for_pending` which mints a fresh
  Position via :meth:`PositionTracker.open_from_fill`.
* :class:`EntryManualSink` — does not auto-fill; mirrors the signal to
  a subscriber callback so the GUI can prompt the user to execute the
  entry on a real broker manually.

All sinks satisfy the :class:`EntrySignalSink` Protocol.

Differences from :class:`exits.signals.ExitSignal`
---------------------------------------------------

* No ``position_id`` — the position does not exist yet.
* ``pending_position_id`` is the **future** id minted by the evaluator
  before submission; ``open_from_fill`` will use this id when the order
  fills, so the GUI / audit chain can correlate signal → order → fill →
  position deterministically.
* ``symbol`` is required (the exit signal infers symbol via
  ``position_id``).
* ``position_side`` is ``"long" | "short"``; the side ``OrderSide.BUY``
  means *open long* (NOT cover short). Disambiguation lives on
  ``position_side``.
* ``on_fill_exit_ids`` is propagated through to the resulting
  ``PaperOrder`` so the entries evaluator can declaratively bind exit
  strategies on fill.
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
    Literal,
    Protocol,
)

from ..exits.model import OrderSide

if TYPE_CHECKING:  # pragma: no cover - import-cycle guard
    from ..exits.paper_engine import PaperBrokerEngine
    from .audit import AuditLog

LOG = logging.getLogger(__name__)

__all__ = [
    "EntryOrderKind",
    "EntrySignal",
    "EntrySignalSink",
    "EntryPaperSink",
    "EntryManualSink",
    "EntryManualSignalEvent",
]


class EntryOrderKind(str, Enum):
    """Order kinds the entry evaluator may emit.

    INDICATOR / SCANNER_ALERT triggers collapse to :data:`MARKET` once
    they fire — the evaluator owns the state machinery; the sink only
    sees the resulting market entry.
    """

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@dataclass(frozen=True)
class EntrySignal:
    """A fully-resolved entry instruction.

    Produced by :class:`entries.evaluator.EntryEvaluator` at trigger
    fire time. The evaluator is responsible for resolving:

    * ``qty`` against the strategy's :class:`SizingRule` and the
      reference price (``bar.close`` typically).
    * ``price`` / ``limit_price`` for native LIMIT/STOP/STOP_LIMIT.
    * ``side`` (BUY for long-open, SELL for short-open) — disambiguated
      by ``position_side``.
    * ``pending_position_id`` minted before submission so the eventual
      Position id is known to the audit chain.
    """

    id: str
    strategy_id: str
    pending_position_id: str
    symbol: str
    trigger_id: str
    kind: EntryOrderKind
    side: OrderSide
    position_side: Literal["long", "short"]
    qty: float
    price: float | None = None
    limit_price: float | None = None
    on_fill_exit_ids: tuple[str, ...] = ()
    label: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, **kwargs: Any) -> EntrySignal:
        """Construct with an auto-assigned id."""
        return cls(id=uuid.uuid4().hex, **kwargs)


class EntrySignalSink(Protocol):
    """Protocol every concrete entry sink satisfies.

    Sinks return broker-side order ids on :meth:`submit`; the evaluator
    associates these ids with the originating ``pending_position_id``
    so a later cancel can target either the sink-id or the
    pending-position-id.

    All methods are required to be Tk-thread safe (they run from the
    evaluator which lives on the Tk thread).
    """

    def submit(self, signal: EntrySignal) -> str: ...

    def cancel(self, order_id: str) -> bool: ...

    def cancel_all_pending_for_symbol(self, symbol: str) -> int: ...

    def working_order_ids_for_pending_position(
        self, pending_position_id: str
    ) -> list[str]: ...


# ---------------------------------------------------------------------------
# EntryPaperSink — auto-fill paper trading
# ---------------------------------------------------------------------------


class EntryPaperSink:
    """Translate :class:`EntrySignal` → pending-entry ``PaperOrder``.

    Holds three index maps:

    * forward: ``EntrySignal.id`` → paper order id
    * reverse: paper order id → ``EntrySignal.id``
    * by-pending-position: ``pending_position_id`` → list of paper order ids

    The sink is a pure translator + lookup layer; the engine owns the
    pending-orders index and fill machinery.
    """

    def __init__(self, engine: PaperBrokerEngine) -> None:
        self._engine = engine
        self._signal_to_order: dict[str, str] = {}
        self._order_to_signal: dict[str, str] = {}
        # pending_position_id -> list of paper order ids (typically just one,
        # but we list-it for symmetry with the exits sink).
        self._working_by_pending_pos: dict[str, list[str]] = {}
        # symbol (uppercased) -> list of paper order ids, kept here so we
        # can answer cancel_all_pending_for_symbol after the engine has
        # already fired/cancelled an entry (engine purges its index).
        self._working_by_symbol: dict[str, list[str]] = {}

    def submit(self, signal: EntrySignal) -> str:
        from ..exits.paper_engine import (  # local import to avoid cycle
            OrderTargetKind,
            PaperOrder,
            PaperOrderKind,
        )

        kind_map = {
            EntryOrderKind.MARKET: PaperOrderKind.MARKET,
            EntryOrderKind.LIMIT: PaperOrderKind.LIMIT,
            EntryOrderKind.STOP: PaperOrderKind.STOP,
            EntryOrderKind.STOP_LIMIT: PaperOrderKind.STOP_LIMIT,
        }
        order = PaperOrder(
            id=uuid.uuid4().hex,
            position_id="",  # entries do not bind to an existing position
            kind=kind_map[signal.kind],
            side=signal.side,
            qty=signal.qty,
            price=signal.price,
            limit_price=signal.limit_price,
            label=signal.label,
            extra=dict(signal.extra),
            target_kind=OrderTargetKind.PENDING_ENTRY,
            symbol=signal.symbol,
            pending_position_id=signal.pending_position_id,
            position_side=signal.position_side,
            strategy_id=signal.strategy_id,
            on_fill_exit_ids=signal.on_fill_exit_ids,
        )
        order_id = self._engine.submit(order)
        self._signal_to_order[signal.id] = order_id
        self._order_to_signal[order_id] = signal.id
        self._working_by_pending_pos.setdefault(
            signal.pending_position_id, []
        ).append(order_id)
        self._working_by_symbol.setdefault(signal.symbol.upper(), []).append(
            order_id
        )
        return order_id

    def cancel(self, order_id: str) -> bool:
        cancelled = self._engine.cancel(order_id)
        if cancelled:
            self._forget_order(order_id)
        return cancelled

    def cancel_all_pending_for_symbol(self, symbol: str) -> int:
        sym_key = symbol.upper()
        order_ids = list(self._working_by_symbol.get(sym_key, []))
        n = self._engine.cancel_all_pending_for_symbol(symbol)
        for oid in order_ids:
            self._forget_order(oid)
        # Defensive: drop any stale entry that survived (e.g. due to a
        # double-track bookkeeping bug).
        self._working_by_symbol.pop(sym_key, None)
        return n

    def working_order_ids_for_pending_position(
        self, pending_position_id: str
    ) -> list[str]:
        """Snapshot of working paper order ids for a given pending id.

        Read-only — safe from any thread.
        """
        return list(self._working_by_pending_pos.get(pending_position_id, []))

    def working_order_ids_for_symbol(self, symbol: str) -> list[str]:
        return list(self._working_by_symbol.get(symbol.upper(), []))

    def on_fill(self, order_id: str) -> None:
        """Hook called by the evaluator (or the app) when an order fills.

        Drops the order id from local indexes so subsequent
        :meth:`cancel_all_pending_for_symbol` calls don't try to cancel
        a filled (no-longer-pending) order. Idempotent.
        """
        self._forget_order(order_id)

    def _forget_order(self, order_id: str) -> None:
        sid = self._order_to_signal.pop(order_id, None)
        if sid is not None:
            self._signal_to_order.pop(sid, None)
        for pid, ids in list(self._working_by_pending_pos.items()):
            if order_id in ids:
                ids.remove(order_id)
                if not ids:
                    del self._working_by_pending_pos[pid]
                break
        for sym, ids in list(self._working_by_symbol.items()):
            if order_id in ids:
                ids.remove(order_id)
                if not ids:
                    del self._working_by_symbol[sym]
                break


# ---------------------------------------------------------------------------
# EntryManualSink — surface signal to user, do not auto-fill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryManualSignalEvent:
    """Event published by :class:`EntryManualSink` to subscribers."""

    kind: str  # "submitted", "cancelled", "ack-fill"
    signal: EntrySignal | None
    order_id: str


class EntryManualSink:
    """Sink that mirrors entry signals to a subscriber callback.

    For users who paper-trade by manually executing entries on a real
    broker. Maintains a working set keyed by synthetic
    ``manual-entry-<uuid>`` ids; the user (via the GUI) calls
    :meth:`acknowledge_fill` to clear an id, or :meth:`cancel` to drop
    it without a fill.

    Tk-free; the GUI subscribes via :meth:`subscribe` and is
    responsible for marshalling onto the Tk thread before drawing.
    """

    def __init__(self, *, audit: AuditLog | None = None) -> None:
        self._audit = audit
        self._working_by_symbol: dict[str, list[str]] = {}
        self._working_by_pending_pos: dict[str, list[str]] = {}
        self._signals_by_id: dict[str, EntrySignal] = {}
        self._subscribers: list[Callable[[EntryManualSignalEvent], None]] = []
        self._lock = threading.Lock()

    def subscribe(
        self, callback: Callable[[EntryManualSignalEvent], None]
    ) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _unsubscribe

    def _emit(self, event: EntryManualSignalEvent) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(event)
            except Exception:  # pragma: no cover - subscriber bug
                LOG.exception("EntryManualSink subscriber raised; continuing")

    def submit(self, signal: EntrySignal) -> str:
        order_id = f"manual-entry-{uuid.uuid4().hex}"
        self._signals_by_id[order_id] = signal
        self._working_by_symbol.setdefault(signal.symbol.upper(), []).append(
            order_id
        )
        self._working_by_pending_pos.setdefault(
            signal.pending_position_id, []
        ).append(order_id)
        if self._audit is not None:
            self._audit.append(
                "entry_submit",
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                trigger_id=signal.trigger_id,
                order_id=order_id,
                qty=signal.qty,
                price=signal.price,
                meta={
                    "sink": "manual",
                    "label": signal.label,
                    "pending_position_id": signal.pending_position_id,
                },
            )
        self._emit(
            EntryManualSignalEvent(
                kind="submitted", signal=signal, order_id=order_id
            )
        )
        return order_id

    def cancel(self, order_id: str) -> bool:
        if order_id not in self._signals_by_id:
            return False
        signal = self._signals_by_id.pop(order_id)
        self._drop_index(signal, order_id)
        if self._audit is not None:
            self._audit.append(
                "entry_cancel",
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                trigger_id=signal.trigger_id,
                order_id=order_id,
                meta={
                    "sink": "manual",
                    "pending_position_id": signal.pending_position_id,
                },
            )
        self._emit(
            EntryManualSignalEvent(
                kind="cancelled", signal=signal, order_id=order_id
            )
        )
        return True

    def cancel_all_pending_for_symbol(self, symbol: str) -> int:
        ids = list(self._working_by_symbol.get(symbol.upper(), []))
        n = 0
        for oid in ids:
            if self.cancel(oid):
                n += 1
        return n

    def acknowledge_fill(self, order_id: str) -> bool:
        """User has executed this entry on a real broker; clear it."""
        if order_id not in self._signals_by_id:
            return False
        signal = self._signals_by_id.pop(order_id)
        self._drop_index(signal, order_id)
        if self._audit is not None:
            self._audit.append(
                "entry_fill",
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                trigger_id=signal.trigger_id,
                order_id=order_id,
                qty=signal.qty,
                price=signal.price,
                meta={
                    "sink": "manual",
                    "ack": True,
                    "pending_position_id": signal.pending_position_id,
                },
            )
        self._emit(
            EntryManualSignalEvent(
                kind="ack-fill", signal=signal, order_id=order_id
            )
        )
        return True

    def working_order_ids_for_pending_position(
        self, pending_position_id: str
    ) -> list[str]:
        return list(self._working_by_pending_pos.get(pending_position_id, []))

    def working_order_ids_for_symbol(self, symbol: str) -> list[str]:
        return list(self._working_by_symbol.get(symbol.upper(), []))

    def _drop_index(self, signal: EntrySignal, order_id: str) -> None:
        sym_key = signal.symbol.upper()
        ids = self._working_by_symbol.get(sym_key, [])
        if order_id in ids:
            ids.remove(order_id)
            if not ids:
                self._working_by_symbol.pop(sym_key, None)
        ids = self._working_by_pending_pos.get(signal.pending_position_id, [])
        if order_id in ids:
            ids.remove(order_id)
            if not ids:
                self._working_by_pending_pos.pop(signal.pending_position_id, None)
