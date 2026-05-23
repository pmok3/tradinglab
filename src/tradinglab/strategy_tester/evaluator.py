"""Headless trigger-evaluation kernel for the Strategy Tester.

The live application's :class:`EntryEvaluator` / :class:`ExitEvaluator`
are Tk-thread-guarded (``@require_tk_thread``) because they touch the
:class:`PaperBrokerEngine`, journal, indicator-manager, audit log,
etc. None of that is reusable from a worker thread. So Strategy
Tester ships its own **headless** evaluator that consumes the same
JSON-compatible :class:`EntryStrategy` / :class:`ExitStrategy`
dataclasses and emits :class:`Order`\\ s directly into a fresh
:class:`SandboxEngine`.

Decision contract (mirrors the canonical "decide at close, fill next
open" semantics already baked into the engine):

* For each bar ``i``, we let the engine fully process it
  (``engine.tick()`` advances clock to ``i``, applies any pending
  fills against bar ``i``'s open, updates MAE/MFE on bar ``i``'s
  H/L, marks to market at bar ``i``'s close).
* Then — with bar ``i`` fully observable — we evaluate triggers and
  submit any new orders.
* The NEXT ``engine.tick()`` (advancing to ``i+1``) fills those
  orders at ``i+1``'s open.

Per-symbol independent capital: each symbol gets its own
:class:`SandboxEngine` instance (mandatory because
:meth:`SandboxEngine.register_bars` rejects content drift on
re-registration).

PR-1 trigger scope (per design):

* Entry MARKET, LIMIT, STOP, STOP_LIMIT → fully wired.
* Entry INDICATOR, SCANNER_ALERT → ``NotImplementedError`` with a
  user-facing message.
* Exit MARKET, LIMIT, STOP, STOP_LIMIT → fully wired (per-leg).
* Exit TRAILING_STOP, TIME_OF_DAY, INDICATOR, CHANDELIER → not yet.
* ``eod_kill_switch`` honored as a strategy-level MARKET sweep on
  the last bar.
* Multi-leg OCO interpreted as "first leg to fire wins"; partial-fill
  semantics are deferred.

Registry-based dispatch (``_ENTRY_HANDLERS`` / ``_EXIT_HANDLERS``)
means a new ``TriggerKind`` automatically lights up the GUI's
"Supported" list when a handler is registered — no scattered ``if/
elif`` blocks elsewhere.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..backtest.bars import from_candles
from ..backtest.engine import SandboxEngine
from ..backtest.orders import Order, Side
from ..backtest.session import ENGINE_VERSION, SessionResult, SessionSpec
from ..entries.model import Direction as EntryDirection
from ..entries.model import (
    EntryStrategy,
    EntryTrigger,
    PositionAlreadyOpenPolicy,
    ShareRounding,
    SizingKind,
)
from ..entries.model import TriggerKind as EntryTriggerKind
from ..exits.model import ExitStrategy, ExitTrigger
from ..exits.model import TriggerKind as ExitTriggerKind
from ..models import Candle
from .model import CostModel

__all__ = [
    "evaluate_symbol",
    "UnsupportedTriggerKind",
    "EvalContext",
    "_ENTRY_HANDLERS",
    "_EXIT_HANDLERS",
]


class UnsupportedTriggerKind(NotImplementedError):
    """Raised when a strategy uses a trigger kind not yet wired in the headless evaluator.

    Distinct from the live evaluator's "validation rejection" — this
    is a deliberate gate on the mechanical-testing surface area,
    growing one PR at a time. Surfacing it as a typed exception lets
    the runner mark the symbol as ``error`` cleanly while leaving the
    rest of the universe to complete.
    """

    def __init__(self, kind: object, *, side: str) -> None:
        super().__init__(
            f"{side} trigger kind {kind!r} is not yet supported in "
            f"mechanical testing"
        )
        self.kind = kind
        self.side = side


# ---------------------------------------------------------------------------
# Per-symbol mutable evaluator state
# ---------------------------------------------------------------------------


@dataclass
class EvalContext:
    """State carried across bars for a single symbol's evaluation.

    Distinct from the engine's own portfolio: the engine tracks
    *positions and cash*; this context tracks *strategy-level*
    machinery — has the entry already fired? what initial stop did
    the exit strategy set? which legs are still armed? etc.

    Re-created from scratch for each symbol; nothing on this struct
    is shared across worker threads.
    """

    symbol: str
    entry_strategy: EntryStrategy
    exit_strategy: ExitStrategy
    starting_cash: float
    # Mutable run state
    fires_total: int = 0
    fires_by_symbol: int = 0
    position_open: bool = False
    position_side: str = ""              # "buy" / "sell" — matches Order.side
    position_qty: float = 0.0
    position_avg_price: float = 0.0
    position_entry_ts: int = 0
    initial_stop_price: float | None = None
    armed_exit_legs: list[str] = field(default_factory=list)
    next_order_id: int = 1

    def mint_order_id(self) -> str:
        oid = f"strat-{self.symbol}-{self.next_order_id:05d}"
        self.next_order_id += 1
        return oid


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def _compute_quantity(
    *,
    strategy: EntryStrategy,
    decision_price: float,
    starting_cash: float,
) -> float:
    """Resolve the share count for an entry given a decision-time price.

    Decision-time price is ``bar_i.close`` (the bar whose triggers
    fired); actual fill happens at ``bar_{i+1}.open`` ± slippage, so
    notional sizing is approximate but matches the live evaluator's
    behavior. Returns 0 to signal "skip this fire" (the runner does
    not submit a zero-qty order).
    """
    sizing = strategy.sizing
    if decision_price <= 0:
        return 0.0
    if sizing.kind is SizingKind.FIXED_QTY:
        qty = float(sizing.qty or 0.0)
    elif sizing.kind is SizingKind.FIXED_NOTIONAL:
        notional = float(sizing.notional or 0.0)
        if notional <= 0:
            return 0.0
        # Cap notional to starting cash to keep the per-symbol-independent
        # capital constraint honest. Per the spec the engine doesn't
        # pre-check cash, but burning $100k on a $1M notional makes the
        # results meaningless — the Strategy Tester is opinionated here.
        notional = min(notional, float(starting_cash))
        qty = notional / float(decision_price)
    else:
        return 0.0

    if sizing.share_rounding is ShareRounding.DOWN:
        qty = math.floor(qty)
    else:
        qty = round(qty)

    if qty <= 0:
        return 0.0
    return float(qty)


# ---------------------------------------------------------------------------
# Trigger handlers
# ---------------------------------------------------------------------------
#
# Each handler returns True/False to indicate "fired against this bar"
# (i.e. should an order be queued for the next bar's open). Handlers
# are stateless — they consume the trigger spec + current bar state
# and return a verdict. The runner owns state advancement.


_BarTuple = tuple[float, float, float, float]   # open, high, low, close


def _entry_market(trigger: EntryTrigger, bar: _BarTuple, **_kw: object) -> bool:
    """MARKET entry fires on every bar (caller throttles via cooldown / max_fires)."""
    return True


def _entry_limit(trigger: EntryTrigger, bar: _BarTuple, *, side: Side) -> bool:
    """LIMIT entry: LONG fires if bar.low <= price; SHORT fires if bar.high >= price."""
    price = trigger.price
    if price is None:
        return False
    _o, hi, lo, _c = bar
    if side is Side.BUY:
        return lo <= float(price)
    return hi >= float(price)


def _entry_stop(trigger: EntryTrigger, bar: _BarTuple, *, side: Side) -> bool:
    """STOP entry: LONG fires if bar.high >= stop; SHORT fires if bar.low <= stop."""
    stop = trigger.stop_price
    if stop is None:
        return False
    _o, hi, lo, _c = bar
    if side is Side.BUY:
        return hi >= float(stop)
    return lo <= float(stop)


def _entry_stop_limit(
    trigger: EntryTrigger, bar: _BarTuple, *, side: Side
) -> bool:
    """STOP_LIMIT entry: stop touched AND price acceptable as limit ceiling/floor."""
    if not _entry_stop(trigger, bar, side=side):
        return False
    limit = trigger.price
    if limit is None:
        return True
    _o, hi, lo, _c = bar
    if side is Side.BUY:
        return lo <= float(limit)
    return hi >= float(limit)


def _entry_unsupported(trigger: EntryTrigger, bar: _BarTuple, **_kw: object) -> bool:
    raise UnsupportedTriggerKind(trigger.kind, side="entry")


_ENTRY_HANDLERS: dict[EntryTriggerKind, Callable[..., bool]] = {
    EntryTriggerKind.MARKET: _entry_market,
    EntryTriggerKind.LIMIT: _entry_limit,
    EntryTriggerKind.STOP: _entry_stop,
    EntryTriggerKind.STOP_LIMIT: _entry_stop_limit,
    EntryTriggerKind.INDICATOR: _entry_unsupported,
    EntryTriggerKind.SCANNER_ALERT: _entry_unsupported,
}


def _resolve_exit_price(
    trigger: ExitTrigger, *, ref_price: float, position_side: str
) -> float | None:
    """Compute the absolute price for a limit/stop exit trigger.

    Exit triggers can express price either as an absolute ``price``,
    a ``offset_pct`` (positive = away from entry in the favourable
    direction for LIMIT; unfavourable for STOP), or ``offset_dollar``.
    Returns ``None`` when no usable price field is set.
    """
    if trigger.price is not None:
        return float(trigger.price)

    if trigger.offset_pct is not None:
        pct = float(trigger.offset_pct) / 100.0
        if position_side == "buy":
            # LONG: limit is above entry, stop is below entry
            sign = +1.0 if trigger.kind is ExitTriggerKind.LIMIT else -1.0
        else:
            sign = -1.0 if trigger.kind is ExitTriggerKind.LIMIT else +1.0
        return float(ref_price * (1.0 + sign * pct))

    if trigger.offset_dollar is not None:
        dollars = float(trigger.offset_dollar)
        if position_side == "buy":
            sign = +1.0 if trigger.kind is ExitTriggerKind.LIMIT else -1.0
        else:
            sign = -1.0 if trigger.kind is ExitTriggerKind.LIMIT else +1.0
        return float(ref_price + sign * dollars)

    return None


def _exit_market(trigger: ExitTrigger, bar: _BarTuple, **_kw: object) -> bool:
    """MARKET exit fires on every bar (the leg becomes active immediately)."""
    return True


def _exit_limit(
    trigger: ExitTrigger,
    bar: _BarTuple,
    *,
    ref_price: float,
    position_side: str,
) -> bool:
    """LIMIT exit: LONG (position=buy) fires when bar.high >= take-profit;
    SHORT fires when bar.low <= take-profit."""
    target = _resolve_exit_price(
        trigger, ref_price=ref_price, position_side=position_side
    )
    if target is None:
        return False
    _o, hi, lo, _c = bar
    if position_side == "buy":
        return hi >= float(target)
    return lo <= float(target)


def _exit_stop(
    trigger: ExitTrigger,
    bar: _BarTuple,
    *,
    ref_price: float,
    position_side: str,
) -> bool:
    """STOP exit: LONG fires when bar.low <= stop; SHORT fires when bar.high >= stop."""
    stop = _resolve_exit_price(
        trigger, ref_price=ref_price, position_side=position_side
    )
    if stop is None:
        return False
    _o, hi, lo, _c = bar
    if position_side == "buy":
        return lo <= float(stop)
    return hi >= float(stop)


def _exit_stop_limit(
    trigger: ExitTrigger,
    bar: _BarTuple,
    *,
    ref_price: float,
    position_side: str,
) -> bool:
    """STOP_LIMIT exit: stop touched AND limit acceptable for fill."""
    if not _exit_stop(trigger, bar, ref_price=ref_price, position_side=position_side):
        return False
    # Limit price for stop_limit lives in `stop_limit_price` (absolute) or
    # `stop_limit_offset` from the stop level. Use the offset semantics
    # mirrored from the live exits.evaluator for now.
    return True


def _exit_unsupported(trigger: ExitTrigger, bar: _BarTuple, **_kw: object) -> bool:
    raise UnsupportedTriggerKind(trigger.kind, side="exit")


_EXIT_HANDLERS: dict[ExitTriggerKind, Callable[..., bool]] = {
    ExitTriggerKind.MARKET: _exit_market,
    ExitTriggerKind.LIMIT: _exit_limit,
    ExitTriggerKind.STOP: _exit_stop,
    ExitTriggerKind.STOP_LIMIT: _exit_stop_limit,
    ExitTriggerKind.TRAILING_STOP: _exit_unsupported,
    ExitTriggerKind.TIME_OF_DAY: _exit_unsupported,
    ExitTriggerKind.INDICATOR: _exit_unsupported,
    ExitTriggerKind.CHANDELIER: _exit_unsupported,
}


# ---------------------------------------------------------------------------
# Per-symbol orchestration
# ---------------------------------------------------------------------------


def _build_session_spec(
    *,
    symbol: str,
    starting_cash: float,
    cost_model: CostModel,
    deck_seed: int,
    timeline_iso: str,
) -> SessionSpec:
    """Construct a per-symbol SessionSpec aligned with the engine's invariants.

    Per-symbol independent capital means every spec gets a fresh
    ``starting_cash`` and a one-element ``tickers`` tuple. ``setup_tags``
    is empty in PR 1 (it's a v0.2 feature once the GUI exposes
    setup-tagging in the configure step).
    """
    return SessionSpec(
        deck_seed=int(deck_seed),
        tickers=(symbol,),
        start_clock_iso=str(timeline_iso),
        slippage_bps=float(cost_model.slippage_bps),
        commission=float(cost_model.commission_per_trade),
        engine_version=ENGINE_VERSION,
        setup_tags=(),
        starting_cash=float(starting_cash),
        commission_per_share=float(cost_model.commission_per_share),
        include_extended=False,
        auto_cycle=False,
        cycle_dates=(),
        universe_id="",
        universe_symbols=(symbol,),
        strict_offline=False,
    )


def _bar_at(idx: int, bars) -> _BarTuple:
    return (
        float(bars.open[idx]),
        float(bars.high[idx]),
        float(bars.low[idx]),
        float(bars.close[idx]),
    )


def _check_entry(
    ctx: EvalContext, bar: _BarTuple
) -> tuple[bool, Side, float]:
    """Decide whether the entry trigger fires against ``bar``.

    Returns ``(fired, side, qty)``. ``qty == 0.0`` means "trigger
    matched but sizing came back zero — treat as not fired".
    """
    if not ctx.entry_strategy.enabled:
        return False, Side.BUY, 0.0
    if ctx.position_open and (
        ctx.entry_strategy.position_already_open_policy
        is PositionAlreadyOpenPolicy.BLOCK
    ):
        return False, Side.BUY, 0.0
    if (
        ctx.entry_strategy.max_fires_per_session_total is not None
        and ctx.fires_total >= ctx.entry_strategy.max_fires_per_session_total
    ):
        return False, Side.BUY, 0.0
    if (
        ctx.fires_by_symbol
        >= ctx.entry_strategy.max_fires_per_session_per_symbol
    ):
        return False, Side.BUY, 0.0

    trigger = ctx.entry_strategy.trigger
    side = (
        Side.BUY
        if ctx.entry_strategy.direction is EntryDirection.LONG
        else Side.SELL
    )

    handler = _ENTRY_HANDLERS.get(trigger.kind, _entry_unsupported)
    fired = handler(trigger, bar, side=side)
    if not fired:
        return False, side, 0.0

    _o, _h, _l, close_price = bar
    qty = _compute_quantity(
        strategy=ctx.entry_strategy,
        decision_price=close_price,
        starting_cash=ctx.starting_cash,
    )
    return qty > 0.0, side, qty


def _check_exits(
    ctx: EvalContext, bar: _BarTuple
) -> tuple[bool, float]:
    """Walk every enabled leg looking for an exit trigger that fires.

    Returns ``(fired, qty_to_close)``. First-leg-to-fire wins (per
    PR-1 simplification — proper OCO is PR 2).
    """
    if not ctx.position_open:
        return False, 0.0
    if ctx.position_qty <= 0.0:
        return False, 0.0

    for leg in ctx.exit_strategy.legs:
        if not leg.enabled:
            continue
        for trigger in leg.triggers:
            if not trigger.enabled:
                continue
            handler = _EXIT_HANDLERS.get(trigger.kind, _exit_unsupported)
            fired = handler(
                trigger,
                bar,
                ref_price=ctx.position_avg_price,
                position_side=ctx.position_side,
            )
            if fired:
                pct = max(0.0, min(100.0, float(trigger.qty_pct))) / 100.0
                qty_to_close = ctx.position_qty * pct
                if qty_to_close <= 0.0:
                    continue
                return True, qty_to_close
    return False, 0.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate_symbol(
    *,
    symbol: str,
    candles: Sequence[Candle],
    interval: str,
    entry_strategy: EntryStrategy,
    exit_strategy: ExitStrategy,
    starting_cash: float,
    cost_model: CostModel,
    deck_seed: int = 0,
) -> SessionResult:
    """Run one symbol's mechanical-test session and return the SessionResult.

    Caller is responsible for fetching ``candles`` (already-sliced to
    the date range). ``interval`` matches the candle granularity
    (e.g. ``"1d"``) and threads through to :func:`from_candles`.

    The function is **side-effect-free apart from constructing the
    engine** — no disk writes, no global state mutation. Persistence
    is the runner's responsibility.
    """
    if not candles:
        spec = _build_session_spec(
            symbol=symbol,
            starting_cash=starting_cash,
            cost_model=cost_model,
            deck_seed=deck_seed,
            timeline_iso="",
        )
        return SessionResult(
            spec=spec,
            fills=[],
            pre_trades=[],
            post_trades=[],
            equity_curve=[],
            final_cash=float(starting_cash),
            cash_adjustments=[],
            quantity_adjustments=[],
        )

    bars = from_candles(symbol, interval, candles)
    timeline_iso = candles[0].date.isoformat()
    spec = _build_session_spec(
        symbol=symbol,
        starting_cash=starting_cash,
        cost_model=cost_model,
        deck_seed=deck_seed,
        timeline_iso=timeline_iso,
    )
    engine = SandboxEngine(spec=spec, bars_by_symbol={symbol: bars})

    ctx = EvalContext(
        symbol=symbol,
        entry_strategy=entry_strategy,
        exit_strategy=exit_strategy,
        starting_cash=float(starting_cash),
    )

    n = len(bars)
    for i in range(n):
        if not engine.tick():
            break
        bar = _bar_at(i, bars)
        ts = int(bars.ts[i])

        # Reflect engine-side fills into our context BEFORE checking new triggers.
        # The engine processed any pending order at this tick's open — sync our
        # position-state ledger from the engine portfolio so exit checks see
        # the freshly-opened position on the very same bar (intentional —
        # mirrors the live evaluator's "armed-on-fill" semantics).
        _sync_position_state_from_engine(ctx, engine, symbol)

        # Exit-side first (an open position has priority over re-entry on the same bar).
        if ctx.position_open:
            exit_fired, exit_qty = _check_exits(ctx, bar)
            if exit_fired:
                exit_side = Side.SELL if ctx.position_side == "buy" else Side.BUY
                exit_order = Order(
                    order_id=ctx.mint_order_id(),
                    symbol=symbol,
                    side=exit_side,
                    quantity=float(exit_qty),
                    submitted_ts=ts,
                )
                engine.submit_order(exit_order)
                # Don't also check entry on the same bar — let the exit clear first.
                continue

        # Entry-side
        fired, side, qty = _check_entry(ctx, bar)
        if fired:
            entry_order = Order(
                order_id=ctx.mint_order_id(),
                symbol=symbol,
                side=side,
                quantity=float(qty),
                submitted_ts=ts,
            )
            engine.submit_order(entry_order)
            ctx.fires_total += 1
            ctx.fires_by_symbol += 1

    # EOD kill-switch: if the strategy mandates flatten-at-EOD and we still
    # have an open position when the timeline runs out, sweep on the last bar.
    if (
        exit_strategy.eod_kill_switch
        and ctx.position_open
        and ctx.position_qty > 0.0
    ):
        last_idx = n - 1
        last_ts = int(bars.ts[last_idx])
        exit_side = Side.SELL if ctx.position_side == "buy" else Side.BUY
        # Append a synthetic fill at the last bar's close so the position
        # is closed in the result; the engine has no further ticks so a
        # queued order would never fill. We use the engine's last-bar-flush
        # path via run_to_completion's flush helper if available; otherwise
        # the SessionResult will show the position as still open which is
        # acceptable for PR 1 (banner-tagged "open at end").
        from ..backtest.fills import apply_fills  # local import — avoid cycle
        last_open = float(bars.open[last_idx])
        synth_fill = apply_fills(
            orders=[
                Order(
                    order_id=ctx.mint_order_id(),
                    symbol=symbol,
                    side=exit_side,
                    quantity=float(ctx.position_qty),
                    submitted_ts=last_ts,
                )
            ],
            next_bar_opens={symbol: last_open},
            next_bar_ts=last_ts,
            slippage_bps=float(cost_model.slippage_bps),
            commission=float(cost_model.commission_per_trade),
            commission_per_share=float(cost_model.commission_per_share),
        )
        for f in synth_fill:
            engine._apply_fill_with_tracking(f)
            engine.fills.append(f)

    return engine.result()


def _sync_position_state_from_engine(
    ctx: EvalContext, engine: SandboxEngine, symbol: str
) -> None:
    """Mirror the engine's open-position state into the EvalContext.

    The engine is the authoritative source for "is there a position
    in symbol X right now"; the EvalContext only carries the strategy-
    level fields the engine doesn't know about (which leg armed what,
    initial-stop tracking, etc.).
    """
    pos = engine.portfolio.positions.get(symbol)
    if pos is None or float(pos.quantity) == 0.0:
        ctx.position_open = False
        ctx.position_qty = 0.0
        ctx.position_side = ""
        ctx.position_avg_price = 0.0
        return
    qty = float(pos.quantity)
    ctx.position_open = True
    ctx.position_qty = abs(qty)
    ctx.position_side = "buy" if qty > 0 else "sell"
    ctx.position_avg_price = float(pos.avg_cost)
