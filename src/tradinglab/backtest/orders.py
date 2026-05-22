"""Order + Fill dataclasses for the sandbox engine.

Phase 1a is market-orders-only. Stops, limits, and bracket orders are
deferred to Phase 2 per the locked decision Q2 ("no stops in MVP").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Order:
    """A market order submitted at ``submitted_ts``.

    Fills land at the *next* bar's open (engine-side semantics; this
    dataclass carries no fill state). ``order_id`` is caller-assigned
    so the journal can refer back to the same id without UUID coupling.
    """
    order_id: str
    symbol: str
    side: Side
    quantity: float
    submitted_ts: int


@dataclass(frozen=True)
class Fill:
    """A completed market fill.

    ``fill_price`` already includes slippage in the worse-fill direction
    (BUY higher, SELL lower). ``commission`` is per-fill currency,
    deducted from cash separately by the portfolio.
    """
    order_id: str
    symbol: str
    side: Side
    quantity: float
    fill_price: float
    fill_ts: int
    slippage_bps: float
    commission: float
