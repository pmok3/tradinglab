"""``PositionTracker`` — Tk-thread registry of open positions.

State
-----

A flat dict keyed by ``Position.id``. Lookups by symbol/side route through
:meth:`list_open_for` which scans the dict.

Subscribers
-----------

Code that needs to react to position events (audit log, Treeview, chart
overlay, exit evaluator) registers a callable via :meth:`subscribe`.
Subscribers are notified from a per-tracker event queue, **not inline**:

1. A mutator method (``open`` / ``apply_fill`` / etc.) appends the event
   to ``self._pending_events`` and calls ``_drain()``.
2. ``_drain()`` iterates a frozen tuple snapshot of subscribers,
   per-event. If a subscriber calls back into a mutator, that mutator
   appends to the same queue (since ``_dispatching`` is True) and returns
   immediately without re-draining.
3. The original ``_drain()`` keeps consuming from the queue until empty.

This ensures (a) re-entrancy doesn't crash on list-mutation-during-iter,
(b) subscriber order is stable per event, (c) nested events fire in
emit-order after the outer event resolves.

Threading
---------

Every public mutator is decorated with ``@require_tk_thread``. Tests can
bypass via :func:`tradinglab.core.thread_guard.tk_thread_check_disabled`.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

from ..core.thread_guard import require_tk_thread
from .model import (
    Position,
    PositionEvent,
    PositionEventKind,
    PositionSide,
    PositionSource,
)

LOG = logging.getLogger(__name__)

Subscriber = Callable[[PositionEvent, Position], None]


class PositionTracker:
    """Single source of truth for open positions during a session."""

    def __init__(self) -> None:
        self._positions: Dict[str, Position] = {}
        self._subscribers: List[Subscriber] = []
        self._pending_events: Deque[Tuple[PositionEvent, Position]] = deque()
        self._dispatching: bool = False

    # ---- queries -----------------------------------------------------

    def get(self, position_id: str) -> Optional[Position]:
        return self._positions.get(position_id)

    def list_open(self) -> List[Position]:
        return [p for p in self._positions.values() if p.is_open]

    def list_open_for(
        self, symbol: str, side: Optional[PositionSide] = None,
    ) -> List[Position]:
        sym = (symbol or "").upper()
        out = [
            p for p in self._positions.values()
            if p.is_open and p.symbol.upper() == sym
            and (side is None or p.side == side)
        ]
        return out

    def __len__(self) -> int:
        return len(self._positions)

    # ---- subscriber API ---------------------------------------------

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        """Register ``fn``; returns an unsubscribe callable.

        Subscribing is allowed from any thread (read-only mutation of the
        subscriber list is guarded). All event delivery still happens on
        the Tk main thread.
        """
        self._subscribers.append(fn)

        def _unsub() -> None:
            try:
                self._subscribers.remove(fn)
            except ValueError:
                pass

        return _unsub

    # ---- mutators: ALL @require_tk_thread ---------------------------

    @require_tk_thread
    def open(
        self,
        *,
        symbol: str,
        side: PositionSide,
        qty: float,
        price: float,
        source: PositionSource,
        ts: Optional[datetime] = None,
        strategy_id: Optional[str] = None,
        extra: Optional[Dict] = None,
        position_id: Optional[str] = None,
    ) -> Position:
        if qty <= 0:
            raise ValueError("qty must be > 0")
        if price <= 0:
            raise ValueError("price must be > 0")
        sym = (symbol or "").upper()
        if not sym:
            raise ValueError("symbol must be non-empty")
        pid = position_id or _new_id()
        if pid in self._positions:
            raise ValueError(f"position id {pid!r} already exists")
        when = ts or _now()
        pos = Position(
            id=pid,
            symbol=sym,
            side=side,
            qty_initial=float(qty),
            qty_open=float(qty),
            avg_entry_price=float(price),
            entry_time=when,
            source=source,
            high_watermark=float(price),
            low_watermark=float(price),
            last_price=float(price),
            strategy_id=strategy_id,
            extra=dict(extra or {}),
        )
        self._positions[pid] = pos
        self._enqueue(PositionEvent(
            position_id=pid, kind=PositionEventKind.OPEN, ts=when,
            qty=qty, price=price, meta={"side": side, "source": source},
        ), pos)
        self._drain()
        return pos

    @require_tk_thread
    def apply_fill(
        self,
        *,
        position_id: str,
        qty: float,
        price: float,
        ts: Optional[datetime] = None,
        meta: Optional[Dict] = None,
    ) -> Position:
        """Apply an exit fill (closing or partially closing the position).

        ``qty`` is unsigned; the fill is interpreted as a CLOSE in the
        position's natural direction (longs sell, shorts buy). For
        scaling INTO a position (entries), use :meth:`add_to_position`
        — but v1 has no entry support so callers shouldn't need it.
        """
        pos = self._positions.get(position_id)
        if pos is None:
            raise KeyError(f"unknown position {position_id!r}")
        if qty <= 0:
            raise ValueError("qty must be > 0")
        if price <= 0:
            raise ValueError("price must be > 0")
        applied = min(float(qty), pos.qty_open)
        if applied <= 0:
            return pos  # already flat — silent no-op
        # Realized PnL: long sold at price > entry = profit; reverse for short.
        delta = (price - pos.avg_entry_price) if pos.side == "long" else (pos.avg_entry_price - price)
        pos.realized_pnl += delta * applied
        pos.qty_open -= applied
        when = ts or _now()
        if pos.qty_open <= 0:
            pos.qty_open = 0.0
            kind = PositionEventKind.CLOSE
        else:
            kind = PositionEventKind.PARTIAL_CLOSE
        ev = PositionEvent(
            position_id=position_id, kind=kind, ts=when,
            qty=applied, price=price, meta=dict(meta or {}),
        )
        self._enqueue(ev, pos)
        # Auto-detach strategy on full close.
        if kind == PositionEventKind.CLOSE and pos.strategy_id is not None:
            sid = pos.strategy_id
            pos.strategy_id = None
            self._enqueue(PositionEvent(
                position_id=position_id,
                kind=PositionEventKind.STRATEGY_UNBIND,
                ts=when,
                meta={"strategy_id": sid, "reason": "position_closed"},
            ), pos)
        self._drain()
        return pos

    @require_tk_thread
    def open_from_fill(
        self,
        *,
        symbol: str,
        side: PositionSide,
        qty: float,
        price: float,
        ts: Optional[datetime] = None,
        source: PositionSource = "sandbox",
        strategy_id: Optional[str] = None,
        position_id: Optional[str] = None,
        fill_meta: Optional[Dict] = None,
    ) -> Position:
        """Mint a brand-new :class:`Position` from a paper-engine entry fill.

        This is the entries-v1 counterpart to :meth:`apply_fill` (which
        only closes positions). It enforces the same invariants as
        :meth:`open` (positive qty/price, non-empty symbol, no duplicate
        ``position_id``) and emits the same ``OPEN`` event so subscribers
        (audit log, exits-v1 tab, chart overlay) react identically to
        manual sandbox opens vs entry-engine opens.

        ``position_id`` is required when the caller wants to correlate
        with a pre-allocated id (the paper engine mints a pending id so
        ``on_fill_exit_ids`` can be bracketed atomically). If omitted,
        a fresh uuid is generated.

        ``fill_meta`` is merged into the OPEN event's ``meta`` dict so
        the audit chain carries through (``order_id``, ``trigger_id``,
        ``bar_ts``, etc.). The ``side``/``source`` keys always win over
        anything provided in ``fill_meta`` to keep the schema stable.

        HARD ERROR on duplicate ``position_id`` — silent dedupe would
        mask audit-chain bugs where the engine accidentally tried to
        promote the same pending order twice.
        """
        if qty <= 0:
            raise ValueError("qty must be > 0")
        if price <= 0:
            raise ValueError("price must be > 0")
        sym = (symbol or "").upper()
        if not sym:
            raise ValueError("symbol must be non-empty")
        pid = position_id or _new_id()
        if pid in self._positions:
            raise ValueError(f"position id {pid!r} already exists")
        when = ts or _now()
        pos = Position(
            id=pid,
            symbol=sym,
            side=side,
            qty_initial=float(qty),
            qty_open=float(qty),
            avg_entry_price=float(price),
            entry_time=when,
            source=source,
            high_watermark=float(price),
            low_watermark=float(price),
            last_price=float(price),
            strategy_id=strategy_id,
            extra={},
        )
        self._positions[pid] = pos
        meta: Dict = dict(fill_meta or {})
        meta["side"] = side
        meta["source"] = source
        if strategy_id is not None:
            meta.setdefault("strategy_id", strategy_id)
        self._enqueue(PositionEvent(
            position_id=pid, kind=PositionEventKind.OPEN, ts=when,
            qty=qty, price=price, meta=meta,
        ), pos)
        self._drain()
        return pos

    @require_tk_thread
    def mark(
        self,
        symbol: str,
        price: float,
        ts: Optional[datetime] = None,
        *,
        bar_close: bool = False,
    ) -> List[Position]:
        """Update last_price + watermarks for all open positions on ``symbol``.

        ``bar_close=True`` increments :attr:`Position.bars_held` by one
        for each affected position.
        """
        if price <= 0:
            return []
        sym = (symbol or "").upper()
        when = ts or _now()
        affected: List[Position] = []
        for pos in self._positions.values():
            if not pos.is_open or pos.symbol.upper() != sym:
                continue
            pos.last_price = float(price)
            if pos.high_watermark <= 0 or price > pos.high_watermark:
                pos.high_watermark = float(price)
            if pos.low_watermark <= 0 or price < pos.low_watermark:
                pos.low_watermark = float(price)
            if bar_close:
                pos.bars_held += 1
            affected.append(pos)
            self._enqueue(PositionEvent(
                position_id=pos.id, kind=PositionEventKind.MARK, ts=when,
                price=price, meta={"bar_close": bar_close},
            ), pos)
        self._drain()
        return affected

    @require_tk_thread
    def bind_strategy(self, position_id: str, strategy_id: str) -> Position:
        pos = self._positions.get(position_id)
        if pos is None:
            raise KeyError(f"unknown position {position_id!r}")
        if not strategy_id:
            raise ValueError("strategy_id must be non-empty")
        if pos.strategy_id == strategy_id:
            return pos
        prior = pos.strategy_id
        pos.strategy_id = strategy_id
        self._enqueue(PositionEvent(
            position_id=position_id,
            kind=PositionEventKind.STRATEGY_BIND,
            ts=_now(),
            meta={"strategy_id": strategy_id, "prior": prior},
        ), pos)
        self._drain()
        return pos

    @require_tk_thread
    def unbind_strategy(self, position_id: str, *, reason: str = "manual") -> Position:
        pos = self._positions.get(position_id)
        if pos is None:
            raise KeyError(f"unknown position {position_id!r}")
        if pos.strategy_id is None:
            return pos
        prior = pos.strategy_id
        pos.strategy_id = None
        self._enqueue(PositionEvent(
            position_id=position_id,
            kind=PositionEventKind.STRATEGY_UNBIND,
            ts=_now(),
            meta={"strategy_id": prior, "reason": reason},
        ), pos)
        self._drain()
        return pos

    @require_tk_thread
    def edit(
        self,
        position_id: str,
        *,
        qty_open: Optional[float] = None,
        avg_entry_price: Optional[float] = None,
        last_price: Optional[float] = None,
        meta: Optional[Dict] = None,
    ) -> Position:
        """Manually edit a paper position (e.g. broker-side qty correction).

        Refuses to edit sandbox positions to avoid contradicting fills
        the engine has booked.
        """
        pos = self._positions.get(position_id)
        if pos is None:
            raise KeyError(f"unknown position {position_id!r}")
        if pos.source != "manual":
            raise ValueError("only manual paper positions may be edited")
        before = (pos.qty_open, pos.avg_entry_price, pos.last_price)
        if qty_open is not None:
            if qty_open < 0:
                raise ValueError("qty_open must be >= 0")
            pos.qty_open = float(qty_open)
        if avg_entry_price is not None:
            if avg_entry_price <= 0:
                raise ValueError("avg_entry_price must be > 0")
            pos.avg_entry_price = float(avg_entry_price)
        if last_price is not None:
            if last_price <= 0:
                raise ValueError("last_price must be > 0")
            pos.last_price = float(last_price)
        self._enqueue(PositionEvent(
            position_id=position_id,
            kind=PositionEventKind.EDIT,
            ts=_now(),
            meta={
                "before": {"qty_open": before[0], "avg_entry_price": before[1], "last_price": before[2]},
                **(dict(meta or {})),
            },
        ), pos)
        self._drain()
        return pos

    @require_tk_thread
    def remove(self, position_id: str) -> Optional[Position]:
        """Drop a position from the registry (used at session end / cleanup)."""
        return self._positions.pop(position_id, None)

    @require_tk_thread
    def clear(self) -> None:
        """Drop every position. Subscribers are NOT notified."""
        self._positions.clear()
        self._pending_events.clear()

    # ---- internals ---------------------------------------------------

    def _enqueue(self, ev: PositionEvent, pos: Position) -> None:
        self._pending_events.append((ev, pos))

    def _drain(self) -> None:
        """Dispatch all pending events to subscribers (re-entrancy-safe)."""
        if self._dispatching:
            return  # nested call: outer drain will continue consuming
        self._dispatching = True
        try:
            while self._pending_events:
                ev, pos = self._pending_events.popleft()
                # Frozen-tuple snapshot so subscriber list mutations during
                # dispatch don't break iteration.
                for sub in tuple(self._subscribers):
                    try:
                        sub(ev, pos)
                    except Exception:  # noqa: BLE001
                        LOG.exception("position subscriber raised; continuing")
        finally:
            self._dispatching = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["PositionTracker", "Subscriber"]
