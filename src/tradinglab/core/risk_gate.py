"""Pre-submit risk gate for entry orders.

A ``RiskGate`` is consulted by the entry evaluator just before
submitting an :class:`tradinglab.entries.signals.EntrySignal` to the
paper broker. If the gate returns a :class:`RiskBlock`, the entry is
suppressed and audited; if it returns ``None`` the entry proceeds.

Gates are pure (no side effects beyond reading the tracker / clock) so
they can be unit-tested in isolation. The default implementation
(:class:`DefaultRiskGate`) supports the five v1 essentials:

- ``daily_loss_limit`` — refuse if ``sum(realized + unrealized) <= limit``.
- ``max_concurrent`` — refuse if ``len(tracker.list_open()) >= limit``.
- ``max_position_notional`` — refuse if signal qty * ref_price > limit.
- ``no_new_entries_after`` — refuse if local-clock wall time >= cutoff.
- ``per_symbol_max_notional`` — refuse if existing exposure in this
  symbol + new exposure > limit.

All limits are optional; ``None`` means "no constraint".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime

# Type-only import to avoid an entries -> core -> entries cycle at runtime.
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from ..entries.signals import EntrySignal
    from ..positions.tracker import PositionTracker


__all__ = [
    "RiskBlock",
    "RiskGate",
    "DefaultRiskGate",
    "AllowAllRiskGate",
]


@dataclass(frozen=True)
class RiskBlock:
    """An immutable record of why a gate refused a signal.

    ``gate`` names the rule (e.g. ``"daily_loss_limit"``); ``reason`` is
    a human-readable string for the audit log; ``meta`` carries
    structured numbers (limits, current values) so a UI can render
    "$1,240 daily loss vs $500 limit" without re-deriving from the
    reason text.
    """

    gate: str
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)


class RiskGate(Protocol):
    """Protocol for any pre-submit risk gate."""

    def check(
        self,
        signal: EntrySignal,
        *,
        tracker: PositionTracker,
        clock: Callable[[], datetime],
    ) -> RiskBlock | None:
        """Return :class:`RiskBlock` to refuse, or ``None`` to allow."""
        ...


@dataclass
class AllowAllRiskGate:
    """Trivial gate that approves everything. For tests / opt-out."""

    def check(
        self,
        signal: EntrySignal,
        *,
        tracker: PositionTracker,
        clock: Callable[[], datetime],
    ) -> RiskBlock | None:
        return None


@dataclass
class DefaultRiskGate:
    """The five v1-essential pre-submit gates.

    All limits are optional; setting any to ``None`` disables that
    specific check. Every block returns a :class:`RiskBlock` whose
    ``meta`` carries enough numbers for the audit log / UI to render a
    self-explanatory message.
    """

    daily_loss_limit: float | None = None  # negative number, e.g. -500.0
    max_concurrent: int | None = None
    max_position_notional: float | None = None
    no_new_entries_after: dtime | None = None  # local clock cutoff
    per_symbol_max_notional: float | None = None

    def check(
        self,
        signal: EntrySignal,
        *,
        tracker: PositionTracker,
        clock: Callable[[], datetime],
    ) -> RiskBlock | None:
        # 1. daily_loss_limit (negative threshold)
        if self.daily_loss_limit is not None:
            total_pnl = sum(
                pos.realized_pnl + pos.unrealized_pnl()
                for pos in tracker.list_open()
            )
            if total_pnl <= self.daily_loss_limit:
                return RiskBlock(
                    gate="daily_loss_limit",
                    reason=(
                        f"daily P&L {total_pnl:.2f} <= limit "
                        f"{self.daily_loss_limit:.2f}"
                    ),
                    meta={
                        "current": float(total_pnl),
                        "limit": float(self.daily_loss_limit),
                    },
                )

        # 2. max_concurrent
        if self.max_concurrent is not None:
            current = len(tracker.list_open())
            if current >= self.max_concurrent:
                return RiskBlock(
                    gate="max_concurrent",
                    reason=(
                        f"open positions {current} >= limit "
                        f"{self.max_concurrent}"
                    ),
                    meta={"current": current, "limit": self.max_concurrent},
                )

        # 3. max_position_notional
        if self.max_position_notional is not None:
            ref_price = _ref_price(signal)
            notional = abs(float(signal.qty)) * ref_price
            if notional > self.max_position_notional:
                return RiskBlock(
                    gate="max_position_notional",
                    reason=(
                        f"position notional {notional:.2f} > limit "
                        f"{self.max_position_notional:.2f}"
                    ),
                    meta={
                        "current": notional,
                        "limit": float(self.max_position_notional),
                        "qty": float(signal.qty),
                        "ref_price": ref_price,
                    },
                )

        # 4. no_new_entries_after
        if self.no_new_entries_after is not None:
            now = clock()
            if now.time() >= self.no_new_entries_after:
                return RiskBlock(
                    gate="no_new_entries_after",
                    reason=(
                        f"clock {now.time().strftime('%H:%M')} >= cutoff "
                        f"{self.no_new_entries_after.strftime('%H:%M')}"
                    ),
                    meta={
                        "now": now.time().strftime("%H:%M:%S"),
                        "cutoff": self.no_new_entries_after.strftime("%H:%M:%S"),
                    },
                )

        # 5. per_symbol_max_notional
        if self.per_symbol_max_notional is not None:
            sym = (signal.symbol or "").upper()
            ref_price = _ref_price(signal)
            existing = sum(
                pos.qty_open * (pos.last_price or pos.avg_entry_price)
                for pos in tracker.list_open()
                if pos.symbol.upper() == sym
            )
            new_exposure = abs(float(signal.qty)) * ref_price
            total = existing + new_exposure
            if total > self.per_symbol_max_notional:
                return RiskBlock(
                    gate="per_symbol_max_notional",
                    reason=(
                        f"{sym} total exposure {total:.2f} > limit "
                        f"{self.per_symbol_max_notional:.2f}"
                    ),
                    meta={
                        "symbol": sym,
                        "existing": existing,
                        "new": new_exposure,
                        "total": total,
                        "limit": float(self.per_symbol_max_notional),
                    },
                )

        return None


def _ref_price(signal: EntrySignal) -> float:
    """Pick the best price reference from a signal for notional math.

    Order of preference: explicit ``price`` (LIMIT / STOP_LIMIT), then
    ``stop_price`` (STOP), then a meta key ``ref_price`` (which
    evaluators may stuff with bar.close for MARKET / INDICATOR /
    SCANNER_ALERT triggers). Falls back to 0.0 — gates that rely on a
    price reference will then no-op rather than crash.
    """
    p = getattr(signal, "price", None)
    if p is not None and p > 0:
        return float(p)
    sp = getattr(signal, "stop_price", None)
    if sp is not None and sp > 0:
        return float(sp)
    # Prefer the conventional `extra` dict; fall back to legacy `meta`
    # (some signal types may use either name).
    extra = getattr(signal, "extra", None) or getattr(signal, "meta", None) or {}
    rp = extra.get("ref_price")
    if rp is not None and rp > 0:
        return float(rp)
    return 0.0
