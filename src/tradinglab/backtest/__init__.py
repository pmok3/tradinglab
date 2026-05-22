"""Headless backtest / sandbox kernel.

Phase 1a — pure-Python kernel for the discretionary bar-replay sandbox.

Design contract:
    * The engine is the *single source of truth* for clock advancement,
      order intake, fills, portfolio state, and journal entries.
    * The engine consumes :class:`BarSeries` (per-field ``np.ndarray``);
      it never sees :class:`tradinglab.models.Candle`. The
      ``BarSeries.from_candles`` adapter runs once at session start and
      its result is cached.
    * The engine is synchronous and runs on whatever thread its caller
      drives. The sandbox UI advances it from the Tk main thread; the
      Phase 2 automated batch runner will move it to a worker pool.
    * Every behaviour observable from the kernel must be expressible in
      ``(SessionSpec, bars_by_symbol) → SessionResult``: same input,
      byte-identical JSON-serialised output.

Public API (Phase 1a):
    BarSeries, from_candles
    Clock
    Side, Order, Fill, apply_fills
    Position, Portfolio
    PreTradeEntry, PostTradeReview
    SessionSpec, SessionResult, ENGINE_VERSION
    SandboxEngine
"""

from .bars import BarSeries, from_candles
from .clock import Clock
from .engine import SandboxEngine
from .fills import apply_fills
from .journal import PostTradeReview, PreTradeEntry
from .orders import Fill, Order, Side
from .portfolio import Portfolio, Position
from .session import ENGINE_VERSION, SessionResult, SessionSpec

__all__ = (
    "BarSeries", "from_candles",
    "Clock",
    "Side", "Order", "Fill", "apply_fills",
    "Position", "Portfolio",
    "PreTradeEntry", "PostTradeReview",
    "SessionSpec", "SessionResult", "ENGINE_VERSION",
    "SandboxEngine",
)
