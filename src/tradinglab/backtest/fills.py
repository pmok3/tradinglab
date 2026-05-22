"""Pure-function fill model.

Given a list of pending market orders and the next bar's open price for
each symbol, return the resulting fills. Slippage is applied in the
worse-fill direction: BUY pays open + slip, SELL receives open - slip.

Determinism contract:
    For a fixed ``(orders, opens, slippage_bps, commission)`` tuple the
    output Fill list is byte-identical across calls. Required for the
    Q-12 reproducibility commitment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .orders import Fill, Order, Side


def apply_fills(
    orders: Sequence[Order],
    next_bar_opens: Mapping[str, float],
    next_bar_ts: int,
    slippage_bps: float,
    commission: float,
) -> list[Fill]:
    """Build fills for every order whose symbol has a next-bar open."""
    out: list[Fill] = []
    slip_frac = float(slippage_bps) / 10_000.0
    for o in orders:
        px = next_bar_opens.get(o.symbol)
        if px is None:
            continue
        slip = float(px) * slip_frac
        if o.side is Side.BUY:
            fill_px = float(px) + slip
        else:
            fill_px = float(px) - slip
        out.append(Fill(
            order_id=o.order_id,
            symbol=o.symbol,
            side=o.side,
            quantity=float(o.quantity),
            fill_price=fill_px,
            fill_ts=int(next_bar_ts),
            slippage_bps=float(slippage_bps),
            commission=float(commission),
        ))
    return out
