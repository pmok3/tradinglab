"""Reproducibility-grade session spec + result records.

A :class:`SessionSpec` is everything needed to deterministically replay
a sandbox session: deck seed, ticker list, slippage/commission model,
engine version. Run the engine twice with the same SessionSpec and the
same input bars and you get a byte-identical :class:`SessionResult`
JSON. This is the contract that future leaderboards / walk-forward
analysis will rely on, locked in from day one per the skeptic critique.

JSON round-trip:
    SessionSpec  → ``to_dict()`` / ``from_dict(...)``
    SessionResult → ``to_dict()`` / ``from_dict(...)``

Both follow a canonical key order so ``json.dumps(..., sort_keys=False,
default=...)`` produces stable bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .actions import CashAdjustment, QuantityAdjustment
from .journal import PostTradeReview, PreTradeEntry
from .orders import Fill, Side

ENGINE_VERSION: str = "sandbox-1d"


@dataclass(frozen=True)
class SessionSpec:
    """Reproducibility envelope for a sandbox session.

    ``start_clock_iso`` records *intent* — the tickers' bar data still
    determines the actual timeline, but the spec captures what the user
    asked for (e.g. session-day cursor on 2025-04-29 09:30 ET).

    Phase 1d additions (default-safe, optional in JSON for back-compat):

    * ``include_extended`` — false means the sandbox replay omits
      pre / post-market bars at the master-timeline + per-symbol
      level. Default false matches the day-trader UX the sandbox was
      designed around.
    * ``auto_cycle`` — when true, ``next_bar()`` past end-of-data
      auto-rotates to the next eligible date and continues replay
      until the user explicitly ends the session.
    * ``cycle_dates`` — the ordered (deck_seed-shuffled) list of
      session dates the controller will cycle through. Captured at
      start so the saved session re-plays the same cycle order.
    """
    deck_seed: int
    tickers: tuple[str, ...]
    start_clock_iso: str
    slippage_bps: float
    commission: float
    engine_version: str = ENGINE_VERSION
    setup_tags: tuple[str, ...] = ()
    starting_cash: float = 100_000.0
    include_extended: bool = False
    auto_cycle: bool = False
    cycle_dates: tuple[str, ...] = ()
    # Phase: sandbox universe preload + strict-offline seal.
    # ``universe_id`` is the manifest ID this session was anchored on
    # (e.g. ``"sp500"``, ``"qqq"``, ``"watchlist:Mega Caps"``); empty
    # string means legacy unrestricted mode (any ticker may be loaded
    # mid-session via live fetch). ``universe_symbols`` is the
    # frozen membership snapshot at session-start time, recorded so
    # a saved session captures *what was allowed*, not just *what
    # was traded* (``tickers`` is the latter). ``strict_offline``
    # being True means live fetch was forbidden during the session
    # and out-of-universe tickers were rejected. All three default
    # to back-compat-safe empty / False so old saved sessions round
    # -trip cleanly.
    universe_id: str = ""
    universe_symbols: tuple[str, ...] = ()
    strict_offline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "deck_seed": int(self.deck_seed),
            "tickers": list(self.tickers),
            "start_clock_iso": str(self.start_clock_iso),
            "slippage_bps": float(self.slippage_bps),
            "commission": float(self.commission),
            "engine_version": str(self.engine_version),
            "setup_tags": list(self.setup_tags),
            "starting_cash": float(self.starting_cash),
            "include_extended": bool(self.include_extended),
            "auto_cycle": bool(self.auto_cycle),
            "cycle_dates": list(self.cycle_dates),
            "universe_id": str(self.universe_id),
            "universe_symbols": list(self.universe_symbols),
            "strict_offline": bool(self.strict_offline),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionSpec:
        return cls(
            deck_seed=int(d["deck_seed"]),
            tickers=tuple(d.get("tickers") or ()),
            start_clock_iso=str(d.get("start_clock_iso", "")),
            slippage_bps=float(d.get("slippage_bps", 0.0)),
            commission=float(d.get("commission", 0.0)),
            engine_version=str(d.get("engine_version", ENGINE_VERSION)),
            setup_tags=tuple(d.get("setup_tags") or ()),
            starting_cash=float(d.get("starting_cash", 100_000.0)),
            include_extended=bool(d.get("include_extended", False)),
            auto_cycle=bool(d.get("auto_cycle", False)),
            cycle_dates=tuple(d.get("cycle_dates") or ()),
            universe_id=str(d.get("universe_id", "")),
            universe_symbols=tuple(d.get("universe_symbols") or ()),
            strict_offline=bool(d.get("strict_offline", False)),
        )


@dataclass
class SessionResult:
    """The full output of one engine run."""
    spec: SessionSpec
    fills: list[Fill] = field(default_factory=list)
    pre_trades: list[PreTradeEntry] = field(default_factory=list)
    post_trades: list[PostTradeReview] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    final_cash: float = 0.0
    # Corporate-action records applied by the engine while the session
    # was running. Persisted alongside fills so a loaded session can
    # reproduce held-through-ex-event equity curves byte-for-byte even
    # if the upstream event provider has since revised the underlying
    # EarningsRecord / DividendRecord data. See backtest/actions.py.
    # Additive default-empty so existing sandbox-1d saves load cleanly
    # — no ENGINE_VERSION bump required.
    cash_adjustments: list[CashAdjustment] = field(default_factory=list)
    quantity_adjustments: list[QuantityAdjustment] = field(default_factory=list)

    # ---- serialisation -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "fills": [_fill_to_dict(f) for f in self.fills],
            "pre_trades": [_pre_to_dict(p) for p in self.pre_trades],
            "post_trades": [_post_to_dict(p) for p in self.post_trades],
            "equity_curve": [[int(t), float(v)] for t, v in self.equity_curve],
            "final_cash": float(self.final_cash),
            "cash_adjustments": [_cash_adj_to_dict(a) for a in self.cash_adjustments],
            "quantity_adjustments": [_qty_adj_to_dict(a) for a in self.quantity_adjustments],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionResult:
        return cls(
            spec=SessionSpec.from_dict(d["spec"]),
            fills=[_fill_from_dict(x) for x in d.get("fills") or ()],
            pre_trades=[_pre_from_dict(x) for x in d.get("pre_trades") or ()],
            post_trades=[_post_from_dict(x) for x in d.get("post_trades") or ()],
            equity_curve=[(int(t), float(v)) for t, v in (d.get("equity_curve") or ())],
            final_cash=float(d.get("final_cash", 0.0)),
            cash_adjustments=[_cash_adj_from_dict(x) for x in d.get("cash_adjustments") or ()],
            quantity_adjustments=[_qty_adj_from_dict(x) for x in d.get("quantity_adjustments") or ()],
        )


# ---- field-level helpers (kept private; called from to_dict/from_dict) -----

def _fill_to_dict(f: Fill) -> dict[str, Any]:
    return {
        "order_id": str(f.order_id),
        "symbol": str(f.symbol),
        "side": str(f.side.value),
        "quantity": float(f.quantity),
        "fill_price": float(f.fill_price),
        "fill_ts": int(f.fill_ts),
        "slippage_bps": float(f.slippage_bps),
        "commission": float(f.commission),
    }


def _fill_from_dict(d: dict[str, Any]) -> Fill:
    return Fill(
        order_id=str(d["order_id"]),
        symbol=str(d["symbol"]),
        side=Side(d["side"]),
        quantity=float(d["quantity"]),
        fill_price=float(d["fill_price"]),
        fill_ts=int(d["fill_ts"]),
        slippage_bps=float(d["slippage_bps"]),
        commission=float(d["commission"]),
    )


def _pre_to_dict(p: PreTradeEntry) -> dict[str, Any]:
    return {
        "order_id": str(p.order_id),
        "ts": int(p.ts),
        "symbol": str(p.symbol),
        "side": str(p.side),
        "setup_tag": str(p.setup_tag),
        "thesis": str(p.thesis),
        "conviction": int(p.conviction),
        "size": float(p.size),
        "target": (None if p.target is None else float(p.target)),
        "notes": str(p.notes),
        "next_earnings_ts": int(p.next_earnings_ts),
        "last_earnings_ts": int(p.last_earnings_ts),
        "last_dividend_ts": int(p.last_dividend_ts),
        "last_split_ts": int(p.last_split_ts),
        "earnings_proximity_tag": str(p.earnings_proximity_tag),
        "dividend_proximity_tag": str(p.dividend_proximity_tag),
    }


def _pre_from_dict(d: dict[str, Any]) -> PreTradeEntry:
    target = d.get("target")
    return PreTradeEntry(
        order_id=str(d["order_id"]),
        ts=int(d["ts"]),
        symbol=str(d["symbol"]),
        side=str(d["side"]),
        setup_tag=str(d.get("setup_tag", "")),
        thesis=str(d.get("thesis", "")),
        conviction=int(d.get("conviction", 0)),
        size=float(d.get("size", 0.0)),
        target=(None if target is None else float(target)),
        notes=str(d.get("notes", "")),
        next_earnings_ts=int(d.get("next_earnings_ts", 0)),
        last_earnings_ts=int(d.get("last_earnings_ts", 0)),
        last_dividend_ts=int(d.get("last_dividend_ts", 0)),
        last_split_ts=int(d.get("last_split_ts", 0)),
        earnings_proximity_tag=str(d.get("earnings_proximity_tag", "")),
        dividend_proximity_tag=str(d.get("dividend_proximity_tag", "")),
    )


def _post_to_dict(p: PostTradeReview) -> dict[str, Any]:
    return {
        "symbol": str(p.symbol),
        "entry_ts": int(p.entry_ts),
        "exit_ts": int(p.exit_ts),
        "entry_price": float(p.entry_price),
        "exit_price": float(p.exit_price),
        "quantity": float(p.quantity),
        "side": str(p.side),
        "pnl": float(p.pnl),
        "pnl_pct": float(p.pnl_pct),
        "mae": float(p.mae),
        "mfe": float(p.mfe),
        "mae_pct": float(p.mae_pct),
        "mfe_pct": float(p.mfe_pct),
        "ref_pre_trade_id": (None if p.ref_pre_trade_id is None
                             else str(p.ref_pre_trade_id)),
        "user_review": str(p.user_review),
    }


def _post_from_dict(d: dict[str, Any]) -> PostTradeReview:
    ref = d.get("ref_pre_trade_id")
    return PostTradeReview(
        symbol=str(d["symbol"]),
        entry_ts=int(d["entry_ts"]),
        exit_ts=int(d["exit_ts"]),
        entry_price=float(d["entry_price"]),
        exit_price=float(d["exit_price"]),
        quantity=float(d["quantity"]),
        side=str(d["side"]),
        pnl=float(d["pnl"]),
        pnl_pct=float(d["pnl_pct"]),
        mae=float(d["mae"]),
        mfe=float(d["mfe"]),
        mae_pct=float(d["mae_pct"]),
        mfe_pct=float(d["mfe_pct"]),
        ref_pre_trade_id=(None if ref is None else str(ref)),
        user_review=str(d.get("user_review", "")),
    )


def _cash_adj_to_dict(a: CashAdjustment) -> dict[str, Any]:
    return {
        "ts": int(a.ts),
        "symbol": str(a.symbol),
        "amount_per_share": float(a.amount_per_share),
        "quantity": float(a.quantity),
        "reason": str(a.reason),
        "source_ref": str(a.source_ref),
    }


def _cash_adj_from_dict(d: dict[str, Any]) -> CashAdjustment:
    return CashAdjustment(
        ts=int(d["ts"]),
        symbol=str(d["symbol"]),
        amount_per_share=float(d["amount_per_share"]),
        quantity=float(d["quantity"]),
        reason=str(d.get("reason", "cash_dividend")),
        source_ref=str(d.get("source_ref", "")),
    )


def _qty_adj_to_dict(a: QuantityAdjustment) -> dict[str, Any]:
    return {
        "ts": int(a.ts),
        "symbol": str(a.symbol),
        "ratio_num": int(a.ratio_num),
        "ratio_den": int(a.ratio_den),
        "pre_quantity": float(a.pre_quantity),
        "reason": str(a.reason),
        "source_ref": str(a.source_ref),
    }


def _qty_adj_from_dict(d: dict[str, Any]) -> QuantityAdjustment:
    return QuantityAdjustment(
        ts=int(d["ts"]),
        symbol=str(d["symbol"]),
        ratio_num=int(d["ratio_num"]),
        ratio_den=int(d["ratio_den"]),
        pre_quantity=float(d["pre_quantity"]),
        reason=str(d.get("reason", "stock_split")),
        source_ref=str(d.get("source_ref", "")),
    )
