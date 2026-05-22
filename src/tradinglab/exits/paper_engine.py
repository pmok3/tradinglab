"""``PaperBrokerEngine`` — live in-app broker for paper-trading exit orders.

This engine is **distinct** from
:class:`tradinglab.backtest.engine.SandboxEngine`:

- ``SandboxEngine`` replays a frozen master timeline for backtests.
- ``PaperBrokerEngine`` accepts orders submitted by an upstream evaluator
  and fills them against incoming live bar data.

Scope
-----

The engine handles the four concrete order kinds that have a deterministic
fill price against an OHLC bar: ``MARKET``, ``LIMIT``, ``STOP``, and
``STOP_LIMIT``. Trailing-stop / time-of-day / indicator triggers are
evaluated *upstream* by the :class:`ExitEvaluator`, which decides when
they fire and submits a plain ``MARKET`` :class:`PaperOrder`. The engine
itself doesn't track trail state, indicator state, or wall-clock cutoffs.

Threading
---------

Every public mutator (:meth:`submit`, :meth:`cancel`,
:meth:`cancel_all_for_position`, :meth:`on_bar`) is decorated with
``@require_tk_thread``. Off-thread callers raise
:class:`tradinglab.core.thread_guard.TkThreadViolation`. Tests bypass
the guard via :func:`tk_thread_check_disabled`.

Slippage convention
-------------------

``slippage_bps`` is a fixed deterministic offset applied **against** the
trader on MARKET fills and on STOP fills (which behave like markets once
triggered). A SELL exit has its fill price reduced; a BUY exit has its
fill price raised. Limit fills receive no slippage — by definition the
trader got the limit price they asked for.

Multi-order-per-bar semantics
-----------------------------

Working orders for a position are evaluated in submission (FIFO) order on
each :meth:`on_bar` call. If an early fill closes the position to zero,
later orders **still** evaluate against the bar — but
:meth:`PositionTracker.apply_fill` clamps the applied quantity to the
remaining ``qty_open``, which becomes 0 in that case. The engine records
those "no-op" fills with ``qty=0.0`` so callers see a complete event
trace, but the ``filled`` stats counter only increments on non-zero
quantity. Cancelling sibling orders on full close is not the engine's
job — that's :class:`ExitEvaluator`'s OCO cancellation logic.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from ..core.thread_guard import require_tk_thread
from ..positions.model import PositionSide
from ..positions.tracker import PositionTracker
from .model import OrderSide
from .spec import Bar

LOG = logging.getLogger(__name__)

__all__ = [
    "PaperOrderKind",
    "PaperOrder",
    "Fill",
    "PaperBrokerEngine",
    "OrderTargetKind",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class OrderTargetKind(str, Enum):
    """Whether an order operates on an existing position or mints a new one.

    ``EXISTING_POSITION`` (default) is the original exits-v1 behavior:
    the order references ``position_id`` (which must already exist in
    the tracker), and on fill we call :meth:`PositionTracker.apply_fill`
    to close (or partially close) it.

    ``PENDING_ENTRY`` (entries-v1) flips the relationship: the order
    references a *future* ``pending_position_id`` that does NOT exist
    in the tracker yet. The order also carries ``symbol`` and
    ``position_side`` so we know what to mint. On fill we call
    :meth:`PositionTracker.open_from_fill` to create the new position
    and (the upstream evaluator) optionally bracket it with the
    declarative ``on_fill_exit_ids``.
    """

    EXISTING_POSITION = "existing_position"
    PENDING_ENTRY = "pending_entry"


class PaperOrderKind(str, Enum):
    """The four broker-resolvable order kinds.

    Trailing-stop, time-of-day, and indicator triggers are not present
    here — they are evaluated upstream by ``ExitEvaluator`` which then
    submits a :attr:`MARKET` :class:`PaperOrder` once they fire.
    """

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@dataclass(frozen=True)
class PaperOrder:
    """An immutable broker-side working order.

    For exit orders (``target_kind=EXISTING_POSITION``, the default):
    ``side`` is the *exit* side (SELL closes a long, BUY closes a
    short); ``position_id`` references an existing position in the
    tracker; ``price`` is the trigger price (limit price for LIMIT;
    stop price for STOP / STOP_LIMIT); ``limit_price`` is only
    meaningful for STOP_LIMIT.

    For entry orders (``target_kind=PENDING_ENTRY``):
    ``side`` is the *entry* side (BUY for long-open, SELL for
    short-open); ``position_id`` MAY be empty and ``pending_position_id``
    + ``symbol`` + ``position_side`` are required so the engine can mint
    the new position via :meth:`PositionTracker.open_from_fill` on fill.
    ``on_fill_exit_ids`` declares which exit-strategy ids the upstream
    evaluator should auto-bracket on fill (the bracket-on-fill pattern).
    """

    id: str
    position_id: str
    kind: PaperOrderKind
    side: OrderSide
    qty: float
    price: Optional[float] = None
    limit_price: Optional[float] = None
    label: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Entries-v1 additions (defaults preserve exits-v1 behavior).
    # ------------------------------------------------------------------
    target_kind: OrderTargetKind = OrderTargetKind.EXISTING_POSITION
    symbol: Optional[str] = None
    pending_position_id: Optional[str] = None
    position_side: Optional[PositionSide] = None  # for PENDING_ENTRY
    strategy_id: Optional[str] = None
    on_fill_exit_ids: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class Fill:
    """Synthetic fill record produced when a working order is resolved."""

    order_id: str
    position_id: str
    qty: float
    price: float
    bar_ts: Optional[datetime]
    reason: str
    label: str


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PaperBrokerEngine:
    """Live paper broker that fills exit orders against incoming bars."""

    def __init__(
        self,
        position_tracker: PositionTracker,
        *,
        slippage_bps: float = 0.0,
    ) -> None:
        if slippage_bps < 0:
            raise ValueError("slippage_bps must be >= 0")
        self._tracker = position_tracker
        self._slippage_bps = float(slippage_bps)
        # Insertion order = FIFO submission order. Python dicts preserve
        # insertion order which is exactly the semantics we need.
        self._working: Dict[str, PaperOrder] = {}
        # Symbol-keyed index of pending-entry orders. Populated on
        # submit() when target_kind=PENDING_ENTRY; cleared on fill /
        # cancel. Lets ``on_bar_for_pending(symbol, ...)`` find candidate
        # orders without scanning the full working set.
        self._pending_by_symbol: Dict[str, List[str]] = {}
        self._stats: Dict[str, int] = {
            "working": 0,
            "submitted": 0,
            "filled": 0,
            "cancelled": 0,
            "rejected": 0,
        }

    # ---- queries ----------------------------------------------------

    def working_orders(self) -> List[PaperOrder]:
        """Snapshot of currently working orders, FIFO order."""
        return list(self._working.values())

    def working_orders_for_position(self, position_id: str) -> List[PaperOrder]:
        """Snapshot of currently working orders for ``position_id``.

        Only returns orders with ``target_kind=EXISTING_POSITION`` (the
        original exits-v1 semantics). Pending-entry orders are queried
        via :meth:`pending_orders_for_symbol` instead.
        """
        return [
            o for o in self._working.values()
            if o.position_id == position_id
            and o.target_kind == OrderTargetKind.EXISTING_POSITION
        ]

    def pending_orders_for_symbol(self, symbol: str) -> List[PaperOrder]:
        """Snapshot of pending-entry orders watching ``symbol``."""
        sym = (symbol or "").upper()
        return [
            self._working[oid]
            for oid in self._pending_by_symbol.get(sym, ())
            if oid in self._working
        ]

    def stats(self) -> Dict[str, int]:
        """Counts of working / submitted / filled / cancelled / rejected.

        ``working`` is the size of the in-flight set right now;
        ``submitted`` is the lifetime count of accepted submissions;
        ``filled`` is the lifetime count of non-zero-qty fills;
        ``cancelled`` is the lifetime count of cancellations (including
        cancel_all_for_position cascades); ``rejected`` is the lifetime
        count of failed :meth:`submit` calls.
        """
        snap = dict(self._stats)
        snap["working"] = len(self._working)
        return snap

    # ---- mutators: ALL @require_tk_thread ---------------------------

    @require_tk_thread
    def submit(self, order: PaperOrder) -> str:
        """Validate + accept an order; return its ``order_id``.

        Raises ``ValueError`` (and increments ``rejected``) when the
        order references an unknown position, has non-positive qty, or
        is missing the price field(s) required by its kind.
        """
        try:
            self._validate(order)
        except ValueError:
            self._stats["rejected"] += 1
            raise
        # Generate id if caller provided a sentinel empty string; otherwise
        # respect what they supplied so audit-log routing stays stable.
        if not order.id:
            order = PaperOrder(
                id=uuid.uuid4().hex,
                position_id=order.position_id,
                kind=order.kind,
                side=order.side,
                qty=order.qty,
                price=order.price,
                limit_price=order.limit_price,
                label=order.label,
                extra=dict(order.extra),
            )
        if order.id in self._working:
            self._stats["rejected"] += 1
            raise ValueError(f"order id {order.id!r} already working")
        self._working[order.id] = order
        # Index pending-entry orders by symbol so on_bar_for_pending can
        # find them without a linear scan.
        if order.target_kind == OrderTargetKind.PENDING_ENTRY:
            assert order.symbol is not None  # _validate enforces
            sym = order.symbol.upper()
            self._pending_by_symbol.setdefault(sym, []).append(order.id)
        self._stats["submitted"] += 1
        return order.id

    @require_tk_thread
    def cancel(self, order_id: str) -> bool:
        """Cancel a working order. Returns True iff it existed."""
        order = self._working.pop(order_id, None)
        if order is None:
            return False
        # Drop from pending index if present.
        if order.target_kind == OrderTargetKind.PENDING_ENTRY and order.symbol:
            sym = order.symbol.upper()
            ids = self._pending_by_symbol.get(sym)
            if ids:
                try:
                    ids.remove(order_id)
                except ValueError:
                    pass
                if not ids:
                    del self._pending_by_symbol[sym]
        self._stats["cancelled"] += 1
        return True

    @require_tk_thread
    def cancel_all_for_position(self, position_id: str) -> int:
        """Cancel every working order tagged to ``position_id``.

        Only matches existing-position orders (target_kind=EXISTING_POSITION).
        Pending-entry orders aren't cancelled here — use
        :meth:`cancel_all_pending_for_symbol` or :meth:`cancel` directly.

        Returns the number of orders cancelled.
        """
        ids = [
            oid for oid, o in self._working.items()
            if o.position_id == position_id
            and o.target_kind == OrderTargetKind.EXISTING_POSITION
        ]
        for oid in ids:
            del self._working[oid]
        if ids:
            self._stats["cancelled"] += len(ids)
        return len(ids)

    @require_tk_thread
    def cancel_all_pending_for_symbol(self, symbol: str) -> int:
        """Cancel every pending-entry order watching ``symbol``."""
        sym = (symbol or "").upper()
        ids = list(self._pending_by_symbol.get(sym, ()))
        for oid in ids:
            self._working.pop(oid, None)
        if sym in self._pending_by_symbol:
            del self._pending_by_symbol[sym]
        if ids:
            self._stats["cancelled"] += len(ids)
        return len(ids)

    @require_tk_thread
    def on_bar(
        self,
        position_id: str,
        bar: Bar,
        *,
        is_close: bool,
    ) -> List[Fill]:
        """Evaluate every working order for ``position_id`` against ``bar``.

        Orders are processed in FIFO submission order. Each fill is
        applied to :class:`PositionTracker` immediately so subsequent
        orders on the same bar see the reduced ``qty_open`` (and may
        produce a ``qty=0`` no-op fill if the position has already
        closed). The full event trace — including no-op fills — is
        returned. The ``is_close`` flag is currently informational
        only; both intrabar (forming) and closed bars share the same
        touched-through fill rules. Future enhancements (e.g. honoring
        ``time_in_force``) may use the flag.

        Only existing-position orders are evaluated — pending-entry
        orders for the symbol are evaluated via
        :meth:`on_bar_for_pending`, since they don't have a position_id
        until they fill.
        """
        del is_close  # currently informational; both bar phases fill the same way

        fills: List[Fill] = []
        # Snapshot ids first: the working dict mutates as we fill.
        order_ids = [
            oid for oid, o in self._working.items()
            if o.position_id == position_id
            and o.target_kind == OrderTargetKind.EXISTING_POSITION
        ]
        for oid in order_ids:
            # Re-fetch under the loop: a prior fill in this same on_bar
            # didn't remove this one, but defensive-read is cheap.
            order = self._working.get(oid)
            if order is None:
                continue
            fill = self._try_fill(order, bar)
            if fill is None:
                continue
            # Order resolved (either a real fill or a clamped no-op);
            # remove from working set.
            del self._working[oid]
            applied = self._book_fill(order, fill)
            # The Fill we report reflects what the tracker actually
            # applied (qty may be clamped to 0 if the position closed
            # earlier in this bar).
            reported = Fill(
                order_id=fill.order_id,
                position_id=fill.position_id,
                qty=applied,
                price=fill.price,
                bar_ts=fill.bar_ts,
                reason=fill.reason,
                label=fill.label,
            )
            fills.append(reported)
            if applied > 0:
                self._stats["filled"] += 1
        return fills

    @require_tk_thread
    def on_bar_for_pending(
        self,
        symbol: str,
        bar: Bar,
        *,
        is_close: bool,
    ) -> List[Fill]:
        """Evaluate pending-entry orders for ``symbol`` against ``bar``.

        Mirrors :meth:`on_bar` but for orders with
        ``target_kind=PENDING_ENTRY``. On fill, the engine calls
        :meth:`PositionTracker.open_from_fill` (NOT :meth:`apply_fill`)
        to mint the new position with the order's
        ``pending_position_id`` / ``symbol`` / ``position_side`` /
        ``strategy_id`` / ``on_fill_exit_ids``. The fill record carries
        ``position_id = pending_position_id`` so callers (the entries
        evaluator) can immediately resolve the new position and apply
        the auto-bracket exit ids.

        ``is_close`` is plumbed through but currently informational —
        the entry trigger semantics ("MARKET fires on next closed bar
        after arm") are enforced upstream by the evaluator, which only
        submits MARKET orders when ``is_close=True``.

        Multiple pending orders for the same symbol fill independently
        (a strategy that fires twice produces two distinct positions —
        per the locked design decision "two independent positions").
        """
        del is_close  # currently informational; see docstring
        sym = (symbol or "").upper()
        order_ids = list(self._pending_by_symbol.get(sym, ()))
        if not order_ids:
            return []

        fills: List[Fill] = []
        for oid in order_ids:
            order = self._working.get(oid)
            if order is None:  # pragma: no cover — defensive
                continue
            fill = self._try_fill(order, bar)
            if fill is None:
                continue
            # Resolve the order: drop from working + pending index.
            del self._working[oid]
            ids = self._pending_by_symbol.get(sym)
            if ids:
                try:
                    ids.remove(oid)
                except ValueError:
                    pass
                if not ids:
                    del self._pending_by_symbol[sym]
            applied = self._book_pending_fill(order, fill)
            reported = Fill(
                order_id=fill.order_id,
                # Override position_id to the freshly-minted pending id
                # so downstream callers (the entries evaluator) can
                # bracket it via tracker.get(pending_position_id).
                position_id=order.pending_position_id or "",
                qty=applied,
                price=fill.price,
                bar_ts=fill.bar_ts,
                reason=fill.reason,
                label=fill.label,
            )
            fills.append(reported)
            if applied > 0:
                self._stats["filled"] += 1
        return fills

    # ---- internals --------------------------------------------------

    def _validate(self, order: PaperOrder) -> None:
        if order.qty is None or order.qty <= 0:
            raise ValueError("qty must be > 0")
        if order.kind in (
            PaperOrderKind.LIMIT,
            PaperOrderKind.STOP,
            PaperOrderKind.STOP_LIMIT,
        ) and order.price is None:
            raise ValueError(f"{order.kind.value} order requires price")
        if order.kind == PaperOrderKind.STOP_LIMIT and order.limit_price is None:
            raise ValueError("stop_limit order requires limit_price")
        # Target-specific checks.
        if order.target_kind == OrderTargetKind.EXISTING_POSITION:
            if self._tracker.get(order.position_id) is None:
                raise ValueError(f"unknown position {order.position_id!r}")
        elif order.target_kind == OrderTargetKind.PENDING_ENTRY:
            if not order.symbol:
                raise ValueError("pending_entry order requires symbol")
            if not order.pending_position_id:
                raise ValueError("pending_entry order requires pending_position_id")
            if order.position_side not in ("long", "short"):
                raise ValueError(
                    f"pending_entry order requires position_side in "
                    f"{{long, short}}, got {order.position_side!r}"
                )
            # The pending_position_id MUST NOT already exist in the
            # tracker — that's a hint that the evaluator double-armed
            # the same id.
            if self._tracker.get(order.pending_position_id) is not None:
                raise ValueError(
                    f"pending_position_id {order.pending_position_id!r} "
                    "already exists in tracker"
                )
        else:
            raise ValueError(f"unknown target_kind: {order.target_kind!r}")

    def _try_fill(self, order: PaperOrder, bar: Bar) -> Optional[Fill]:
        if order.kind == PaperOrderKind.MARKET:
            return self._fill_market(order, bar)
        if order.kind == PaperOrderKind.LIMIT:
            return self._fill_limit(order, bar)
        if order.kind == PaperOrderKind.STOP:
            return self._fill_stop(order, bar)
        if order.kind == PaperOrderKind.STOP_LIMIT:
            return self._fill_stop_limit(order, bar)
        return None  # pragma: no cover — exhaustive enum

    def _fill_market(self, order: PaperOrder, bar: Bar) -> Fill:
        price = self._apply_slippage(bar.close, order.side)
        return Fill(
            order_id=order.id,
            position_id=order.position_id,
            qty=order.qty,
            price=price,
            bar_ts=bar.date,
            reason="market",
            label=order.label,
        )

    def _fill_limit(self, order: PaperOrder, bar: Bar) -> Optional[Fill]:
        limit = float(order.price)  # type: ignore[arg-type]
        if order.side == OrderSide.SELL:
            if bar.high < limit:
                return None
            reason = "limit-touched-up"
        else:
            if bar.low > limit:
                return None
            reason = "limit-touched-down"
        return Fill(
            order_id=order.id,
            position_id=order.position_id,
            qty=order.qty,
            price=limit,
            bar_ts=bar.date,
            reason=reason,
            label=order.label,
        )

    def _fill_stop(self, order: PaperOrder, bar: Bar) -> Optional[Fill]:
        stop = float(order.price)  # type: ignore[arg-type]
        if order.side == OrderSide.SELL:
            if bar.low > stop:
                return None
            # Gap-through: open already past the stop -> fill at the
            # worse of (stop, open) for a SELL that's the lower one.
            base = min(stop, bar.open)
            reason = "stop-touched-down"
        else:
            if bar.high < stop:
                return None
            base = max(stop, bar.open)
            reason = "stop-touched-up"
        price = self._apply_slippage(base, order.side)
        return Fill(
            order_id=order.id,
            position_id=order.position_id,
            qty=order.qty,
            price=price,
            bar_ts=bar.date,
            reason=reason,
            label=order.label,
        )

    def _fill_stop_limit(self, order: PaperOrder, bar: Bar) -> Optional[Fill]:
        stop = float(order.price)  # type: ignore[arg-type]
        limit = float(order.limit_price)  # type: ignore[arg-type]
        if order.side == OrderSide.SELL:
            # Stop must be touched...
            if bar.low > stop:
                return None
            # ...and the limit must be reachable on the same bar. For a
            # SELL stop_limit, the limit is the floor below which we
            # won't sell. If the bar's high is below the limit, we
            # never get our price -> stays working.
            if bar.high < limit:
                return None
            reason = "stop-limit-filled-down"
        else:
            if bar.high < stop:
                return None
            # For a BUY stop_limit (cover short), limit is the ceiling
            # above which we won't pay. If the bar's low is above the
            # limit, we never get our price -> stays working.
            if bar.low > limit:
                return None
            reason = "stop-limit-filled-up"
        # Filled at the limit price (no slippage on a limit body).
        return Fill(
            order_id=order.id,
            position_id=order.position_id,
            qty=order.qty,
            price=limit,
            bar_ts=bar.date,
            reason=reason,
            label=order.label,
        )

    def _apply_slippage(self, price: float, side: OrderSide) -> float:
        if self._slippage_bps == 0.0:
            return float(price)
        offset = price * (self._slippage_bps / 10000.0)
        if side == OrderSide.SELL:
            # SELL exit gets a *worse* (lower) fill.
            return float(price) - offset
        # BUY exit gets a *worse* (higher) fill.
        return float(price) + offset

    def _book_fill(self, order: PaperOrder, fill: Fill) -> float:
        """Apply ``fill`` to the position tracker; return applied qty.

        If the position has already been fully closed by an earlier
        order on this bar, ``apply_fill`` becomes a silent no-op and we
        return 0.0. The Fill record is still emitted with ``qty=0`` so
        callers see a complete trace; only the ``filled`` stats counter
        guards against this.
        """
        pos = self._tracker.get(order.position_id)
        if pos is None or pos.qty_open <= 0:
            return 0.0
        applied = min(order.qty, pos.qty_open)
        if applied <= 0:
            return 0.0
        try:
            self._tracker.apply_fill(
                position_id=order.position_id,
                qty=applied,
                price=fill.price,
                ts=fill.bar_ts,
                meta={
                    "order_id": order.id,
                    "label": order.label,
                    "kind": order.kind.value,
                    "reason": fill.reason,
                },
            )
        except (KeyError, ValueError):
            LOG.exception(
                "PaperBrokerEngine: tracker rejected fill for order %s", order.id,
            )
            return 0.0
        return applied

    def _book_pending_fill(self, order: PaperOrder, fill: Fill) -> float:
        """Mint a new :class:`Position` from a pending-entry fill.

        Calls :meth:`PositionTracker.open_from_fill` with the order's
        pre-allocated ``pending_position_id`` so the upstream evaluator
        can immediately bracket-on-fill via the tracker subscription.

        Returns the applied qty (always == ``order.qty`` for a
        successful entry; 0.0 if the tracker rejected the open).
        """
        try:
            self._tracker.open_from_fill(
                symbol=order.symbol,  # type: ignore[arg-type]
                side=order.position_side,  # type: ignore[arg-type]
                qty=order.qty,
                price=fill.price,
                ts=fill.bar_ts,
                source="sandbox",
                strategy_id=order.strategy_id,
                position_id=order.pending_position_id,
                fill_meta={
                    "order_id": order.id,
                    "label": order.label,
                    "kind": order.kind.value,
                    "reason": fill.reason,
                    "on_fill_exit_ids": list(order.on_fill_exit_ids),
                },
            )
        except (KeyError, ValueError):
            LOG.exception(
                "PaperBrokerEngine: tracker rejected pending-entry fill "
                "for order %s (symbol=%s)", order.id, order.symbol,
            )
            return 0.0
        return float(order.qty)
