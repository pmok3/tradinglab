"""Portfolio + Position state for the sandbox engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .orders import Fill, Side


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0.0


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    def get_or_create(self, symbol: str) -> Position:
        pos = self.positions.get(symbol)
        if pos is None:
            pos = Position(symbol=symbol)
            self.positions[symbol] = pos
        return pos

    def apply_fill(self, fill: Fill) -> None:
        """Mutate ``self`` to reflect a single fill.

        Cash flow:
            BUY:  cash -= qty * fill_price + commission
            SELL: cash += qty * fill_price - commission

        Position update is sign-aware: opening/adding uses weighted-avg
        cost; reducing/closing realises P/L; flipping (e.g. selling more
        than the long position) splits into a close + new open at
        ``fill_price``.
        """
        signed_qty = fill.quantity if fill.side is Side.BUY else -fill.quantity
        notional = fill.quantity * fill.fill_price

        if fill.side is Side.BUY:
            self.cash -= notional
        else:
            self.cash += notional
        self.cash -= fill.commission

        pos = self.get_or_create(fill.symbol)
        old_qty = pos.quantity
        new_qty = old_qty + signed_qty

        same_direction = (old_qty == 0.0) or ((old_qty > 0) == (signed_qty > 0))
        if same_direction:
            if new_qty != 0.0:
                pos.avg_cost = (
                    (pos.avg_cost * abs(old_qty)) + (fill.fill_price * abs(signed_qty))
                ) / abs(new_qty)
        else:
            closing_qty = min(abs(signed_qty), abs(old_qty))
            sign_old = 1.0 if old_qty > 0 else -1.0
            pos.realized_pnl += sign_old * (fill.fill_price - pos.avg_cost) * closing_qty
            if abs(signed_qty) > abs(old_qty):
                pos.avg_cost = fill.fill_price
            elif new_qty == 0.0:
                pos.avg_cost = 0.0

        pos.quantity = new_qty

    def mark_to_market(self, ts: int, prices: Mapping[str, float]) -> float:
        """Append (ts, equity) to ``equity_curve`` and return equity."""
        total = self.cash
        for sym, pos in self.positions.items():
            if pos.quantity == 0.0:
                continue
            px = prices.get(sym)
            if px is None:
                px = pos.avg_cost
            total += pos.quantity * float(px)
        self.equity_curve.append((int(ts), float(total)))
        return total
