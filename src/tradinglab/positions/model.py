"""Pure-data dataclasses for the positions package.

A :class:`Position` is a single equity exposure: side, quantity, weighted
entry price, and watermarks. It is mutable so :class:`PositionTracker`
can apply fills and update marks in place; identity is the UUID-string
:attr:`Position.id`.

Watermarks (``high_watermark`` / ``low_watermark``) follow the position's
profitability frontier:

- For a long position, ``high_watermark`` is the maximum ``last_price``
  observed since open, and ``low_watermark`` the minimum.
- For a short, the high/low semantics still track raw price (not
  signed-by-side) — consumers that care about R-multiples or trailing
  anchors should compute their own signed deltas using ``avg_entry_price``
  and ``side``.

A :class:`PositionEvent` is an immutable ledger entry. Every mutation
through :class:`PositionTracker` emits one event so the audit log /
Treeview / chart overlay can render diffs without re-deriving from
position state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Literal, Optional

PositionSide = Literal["long", "short"]
PositionSource = Literal["sandbox", "manual"]


class PositionEventKind(str, Enum):
    """Discriminator for :class:`PositionEvent`. String values are persisted."""

    OPEN = "open"
    PARTIAL_CLOSE = "partial_close"
    CLOSE = "close"
    MARK = "mark"
    STRATEGY_BIND = "strategy_bind"
    STRATEGY_UNBIND = "strategy_unbind"
    EDIT = "edit"  # manual-paper qty / entry edits


@dataclass
class Position:
    """One open or closed equity position. Mutable; identity is :attr:`id`."""

    id: str
    symbol: str
    side: PositionSide
    qty_initial: float
    qty_open: float
    avg_entry_price: float
    entry_time: datetime
    source: PositionSource
    realized_pnl: float = 0.0
    high_watermark: float = 0.0  # max last_price seen since open
    low_watermark: float = 0.0   # min last_price seen since open
    last_price: float = 0.0
    bars_held: int = 0
    strategy_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.qty_open > 0

    def signed_qty_open(self) -> float:
        """Return open quantity signed by side (long positive, short negative)."""
        return self.qty_open if self.side == "long" else -self.qty_open

    def unrealized_pnl(self) -> float:
        """Mark-to-market PnL of the open quantity at :attr:`last_price`.

        For longs: ``(last - entry) * qty_open``.
        For shorts: ``(entry - last) * qty_open``.
        Returns 0.0 if either side of the multiplication is missing.
        """
        if self.qty_open <= 0 or self.avg_entry_price <= 0 or self.last_price <= 0:
            return 0.0
        if self.side == "long":
            return (self.last_price - self.avg_entry_price) * self.qty_open
        return (self.avg_entry_price - self.last_price) * self.qty_open

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "qty_initial": self.qty_initial,
            "qty_open": self.qty_open,
            "avg_entry_price": self.avg_entry_price,
            "entry_time": _iso(self.entry_time),
            "source": self.source,
            "realized_pnl": self.realized_pnl,
            "high_watermark": self.high_watermark,
            "low_watermark": self.low_watermark,
            "last_price": self.last_price,
            "bars_held": self.bars_held,
            "strategy_id": self.strategy_id,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Position":
        return cls(
            id=str(d["id"]),
            symbol=str(d["symbol"]),
            side=_validate_side(d["side"]),
            qty_initial=float(d["qty_initial"]),
            qty_open=float(d["qty_open"]),
            avg_entry_price=float(d["avg_entry_price"]),
            entry_time=_parse_iso(d["entry_time"]),
            source=_validate_source(d["source"]),
            realized_pnl=float(d.get("realized_pnl", 0.0)),
            high_watermark=float(d.get("high_watermark", 0.0)),
            low_watermark=float(d.get("low_watermark", 0.0)),
            last_price=float(d.get("last_price", 0.0)),
            bars_held=int(d.get("bars_held", 0)),
            strategy_id=d.get("strategy_id"),
            extra=dict(d.get("extra", {})),
        )


@dataclass(frozen=True)
class PositionEvent:
    """Immutable ledger entry for a position state change."""

    position_id: str
    kind: PositionEventKind
    ts: datetime
    qty: float = 0.0
    price: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "kind": self.kind.value,
            "ts": _iso(self.ts),
            "qty": self.qty,
            "price": self.price,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PositionEvent":
        return cls(
            position_id=str(d["position_id"]),
            kind=PositionEventKind(d["kind"]),
            ts=_parse_iso(d["ts"]),
            qty=float(d.get("qty", 0.0)),
            price=float(d.get("price", 0.0)),
            meta=dict(d.get("meta", {})),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_side(s: Any) -> PositionSide:
    if s in ("long", "short"):
        return s  # type: ignore[return-value]
    raise ValueError(f"PositionSide must be 'long' or 'short', got {s!r}")


def _validate_source(s: Any) -> PositionSource:
    if s in ("sandbox", "manual"):
        return s  # type: ignore[return-value]
    raise ValueError(f"PositionSource must be 'sandbox' or 'manual', got {s!r}")


def _iso(dt: datetime) -> str:
    """Round-trippable ISO 8601 with explicit UTC offset."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_iso(s: Any) -> datetime:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(s))


__all__ = [
    "Position",
    "PositionEvent",
    "PositionEventKind",
    "PositionSide",
    "PositionSource",
]
