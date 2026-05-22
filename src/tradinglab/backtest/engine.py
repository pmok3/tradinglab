"""``SandboxEngine`` — the headless replay kernel.

Composes :class:`Clock`, :class:`Portfolio`, the pending-order queue,
and the MAE/MFE tracker for open positions. Drives the
locked-decisions contract:

* market-only, fills at next bar's open ± slippage in worse direction
* multi-ticker, multi-position, all symbols advance in lockstep
* synchronous tick model — no autoplay, no threading
* every observable output is captured in a :class:`SessionResult`

The engine is symbol-agnostic: it accepts a dict
``{symbol → BarSeries}`` and constructs its own master timeline as the
sorted union of all symbols' timestamps. Symbols missing a bar at a
given timestamp simply don't have a tradable price for that tick;
fills are silently skipped and mark-to-market falls back to ``avg_cost``
(see :meth:`Portfolio.mark_to_market`). For Phase 1a this covers the
"some tickers don't have pre-market data" case naturally.

Phase 1a is single-threaded and assumes the caller drives ``tick()``
manually. Phase 2's automated batch runner will wrap this in a worker
that ticks until exhaustion without UI involvement; the engine API
doesn't change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from .actions import CashAdjustment, CorporateAction, QuantityAdjustment
from .bars import BarSeries
from .clock import Clock
from .fills import apply_fills
from .journal import PostTradeReview, PreTradeEntry
from .orders import Fill, Order, Side
from .portfolio import Portfolio
from .session import SessionResult, SessionSpec


@dataclass
class _OpenTradeCursor:
    """Tracks the open leg of a position for MAE/MFE accounting.

    One cursor per symbol. Created on the fill that lifts position
    quantity off zero; closed on the fill that drops it back to zero.
    Partial closes/adds keep a single cursor with weighted-avg entry
    price (matched to ``Position.avg_cost``).
    """
    symbol: str
    side: str                      # "buy" for long, "sell" for short
    entry_ts: int
    entry_price: float
    quantity: float                # signed; mirrors Position.quantity
    mae_price: float               # worst price seen against the position
    mfe_price: float               # best price seen for the position
    ref_pre_trade_id: str | None = None


def _bars_fingerprint(bars: BarSeries) -> tuple:
    """Hashable identity for re-registration idempotency.

    Combines length + first/last timestamps + last close. Cheap and
    catches the realistic "different fetch / different cache state"
    re-register cases without requiring a full byte-level compare.
    """
    n = int(len(bars.ts))
    if n == 0:
        return (0, 0, 0, 0.0)
    return (n, int(bars.ts[0]), int(bars.ts[-1]), float(bars.close[-1]))


def _action_fingerprint(a: CorporateAction) -> tuple:
    """Hashable identity for corporate-action idempotency."""
    return (int(a.ts), str(a.kind), float(a.amount),
            int(a.ratio_num), int(a.ratio_den), str(a.source_ref))


@dataclass
class SandboxEngine:
    spec: SessionSpec
    bars_by_symbol: dict[str, BarSeries]
    # Phase 1c-redux: explicit master timeline (calendar-clock model).
    # When ``None`` the timeline is built from the union of every
    # symbol's timestamps (legacy path — used by the f1 reproducibility
    # smoke and any direct programmatic engine construction). When
    # supplied (the open-universe sandbox path), the timeline is frozen
    # at construction; symbols registered later via :meth:`register_bars`
    # do NOT extend it. Per the design critique: a master timeline that
    # mutates mid-session breaks ``clock.index`` semantics.
    master_timeline: np.ndarray | None = None

    # ---- mutable state (filled in __post_init__) -----------------------
    clock: Clock = field(init=False)
    portfolio: Portfolio = field(init=False)
    pending_orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    pre_trades: list[PreTradeEntry] = field(default_factory=list)
    post_trades: list[PostTradeReview] = field(default_factory=list)
    _open_trades: dict[str, _OpenTradeCursor] = field(default_factory=dict)
    _pending_pre_by_order: dict[str, PreTradeEntry] = field(default_factory=dict)
    # Corporate-action queues, keyed by symbol. The full per-symbol
    # list is stored verbatim and never mutated post-register; the
    # drained-set guards against double-application. This separation
    # makes :meth:`register_corporate_actions` idempotent on identical
    # full-content re-register, which is essential for the auto-cycle
    # path that re-builds the engine for the next eligible date.
    # Output records produced by application live in
    # :attr:`cash_adjustments` / :attr:`quantity_adjustments`.
    _pending_actions_by_symbol: dict[str, list[CorporateAction]] = field(default_factory=dict)
    _applied_action_keys: set = field(default_factory=set)
    cash_adjustments: list[CashAdjustment] = field(default_factory=list)
    quantity_adjustments: list[QuantityAdjustment] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.master_timeline is not None:
            timeline = np.asarray(self.master_timeline).astype(np.int64,
                                                               copy=False)
        elif self.bars_by_symbol:
            # Legacy path: master timeline = sorted union of every
            # symbol's timestamps. Used by the headless reproducibility
            # smoke (f1) which constructs the engine directly.
            all_ts = np.concatenate([
                bs.ts for bs in self.bars_by_symbol.values()
            ])
            timeline = np.unique(all_ts).astype(np.int64, copy=False)
        else:
            timeline = np.empty(0, dtype=np.int64)
        self.clock = Clock(timeline=timeline)
        self.portfolio = Portfolio(cash=float(self.spec.starting_cash))

    # ---- mid-session symbol registration -------------------------------

    def register_bars(self, symbol: str, bars: BarSeries) -> bool:
        """Make ``symbol`` tradeable for the rest of the session.

        Idempotent: if ``symbol`` is already registered with the same
        content fingerprint (length + first/last ts + last close), this
        is a no-op and returns ``False``. Different-content
        re-registration is **rejected** with :class:`ValueError` —
        replacing a BarSeries mid-session would retroactively change
        prior fills' MAE/MFE accounting and break reproducibility.

        The master timeline is **not** extended. ``register_bars``
        only adds the per-symbol price source used by fills/MAE/MFE
        on subsequent ticks.
        """
        existing = self.bars_by_symbol.get(symbol)
        if existing is not None:
            if _bars_fingerprint(existing) == _bars_fingerprint(bars):
                return False
            raise ValueError(
                f"symbol {symbol!r} is already registered in this session "
                f"with different content; restart the session to pick up "
                f"updated bars"
            )
        self.bars_by_symbol[symbol] = bars
        return True

    # ---- corporate-action intake ---------------------------------------

    def register_corporate_actions(
        self,
        symbol: str,
        actions: list[CorporateAction],
    ) -> int:
        """Queue ``actions`` to be applied to ``symbol``'s position at the
        engine's corporate-action tick phase.

        Idempotent on (ts, kind, amount, ratio_num, ratio_den, source_ref)
        fingerprint: re-registering the same action list is a no-op.
        Re-registering a *different* action list for an already-populated
        symbol raises :class:`ValueError` for the same reason
        :meth:`register_bars` rejects content drift — silently replacing
        would retroactively change applied adjustments and break
        determinism.

        Empty list is accepted and stored as an empty queue.

        Returns the number of actions registered (0 on idempotent re-call).
        """
        new = sorted(actions, key=lambda a: a.ts)
        existing = self._pending_actions_by_symbol.get(symbol)
        if existing is not None:
            if [_action_fingerprint(a) for a in new] == \
               [_action_fingerprint(a) for a in existing]:
                return 0
            raise ValueError(
                f"corporate actions for {symbol!r} already registered with "
                f"different content; restart the session to pick up changes"
            )
        self._pending_actions_by_symbol[symbol] = new
        return len(new)

    # ---- order intake --------------------------------------------------

    def submit_order(
        self,
        order: Order,
        pre_trade: PreTradeEntry | None = None,
    ) -> None:
        """Queue an order for the next tick. Optionally attach a journal entry.

        The journal entry is recorded against the *order_id* immediately
        so that even an order that never fills (deck-exhausted before
        next tick) is preserved in the session result.
        """
        self.pending_orders.append(order)
        if pre_trade is not None:
            self.pre_trades.append(pre_trade)
            self._pending_pre_by_order[order.order_id] = pre_trade

    # ---- tick --------------------------------------------------------------

    def tick(self) -> bool:
        """Advance one bar. Returns False if the clock was exhausted.

        Three phases, in order:

        1. **Fills** — pending market orders are matched against this
           bar's open ± slippage. Symbols that have no bar at this
           timestamp keep their orders queued for a future tick.
        2. **MAE/MFE** — every still-open position rolls its
           worst/best-seen price using this bar's high and low.
        3. **Mark-to-market** — equity curve point appended using this
           bar's close (or ``avg_cost`` for symbols missing this bar).

        The split into phases mirrors the locked-decisions contract and
        keeps each phase independently testable. Order matters: a fill
        on this tick must contribute to MAE/MFE *from* this bar (its
        entry_price IS this bar's open ± slippage) — opening the cursor
        before phase 2 makes that automatic.
        """
        if not self.clock.tick():
            return False
        ts = self.clock.now_ts
        idx_by_symbol = self._index_by_symbol_at(ts)
        self._process_fills(idx_by_symbol, ts)
        self._update_mae_mfe(idx_by_symbol)
        self._apply_corporate_actions(ts)
        self._mark_to_market(idx_by_symbol, ts)
        return True

    def _index_by_symbol_at(self, ts: int) -> dict[str, int]:
        """Per-symbol bar index at ``ts`` (or absent if symbol has no bar)."""
        out: dict[str, int] = {}
        for sym, bs in self.bars_by_symbol.items():
            i = bs.index_for_ts(ts)
            if i is not None:
                out[sym] = i
        return out

    def _process_fills(
        self,
        idx_by_symbol: Mapping[str, int],
        ts: int,
    ) -> None:
        """Match pending orders against this tick's opens and book fills.

        Orders for symbols absent from ``idx_by_symbol`` (no bar at ``ts``)
        are silently re-queued — Phase 1a contract for symbols missing
        pre-market data.
        """
        if not self.pending_orders:
            return
        opens = {
            sym: float(self.bars_by_symbol[sym].open[i])
            for sym, i in idx_by_symbol.items()
        }
        new_fills = apply_fills(
            orders=self.pending_orders,
            next_bar_opens=opens,
            next_bar_ts=ts,
            slippage_bps=self.spec.slippage_bps,
            commission=self.spec.commission,
        )
        # Drop orders that got filled (or had no open price); requeue
        # symbols absent from this bar.
        filled_ids = {f.order_id for f in new_fills}
        unfilled = [o for o in self.pending_orders
                    if o.order_id not in filled_ids
                    and o.symbol in idx_by_symbol]
        requeue = [o for o in self.pending_orders
                   if o.symbol not in idx_by_symbol]
        self.pending_orders = unfilled + requeue

        for fill in new_fills:
            self._apply_fill_with_tracking(fill)
            self.fills.append(fill)

    def _update_mae_mfe(self, idx_by_symbol: Mapping[str, int]) -> None:
        """Roll worst/best-seen price for every open position using this bar's H/L.

        Long positions: MAE rolls down (worst against = lowest low),
        MFE rolls up (best for = highest high). Shorts are mirrored.
        Symbols with no bar at this tick are skipped — their cursor
        keeps the prior extrema until the next bar at which they trade.
        """
        for sym, cursor in self._open_trades.items():
            i = idx_by_symbol.get(sym)
            if i is None:
                continue
            hi = float(self.bars_by_symbol[sym].high[i])
            lo = float(self.bars_by_symbol[sym].low[i])
            if cursor.side == "buy":
                if lo < cursor.mae_price:
                    cursor.mae_price = lo
                if hi > cursor.mfe_price:
                    cursor.mfe_price = hi
            else:
                if hi > cursor.mae_price:
                    cursor.mae_price = hi
                if lo < cursor.mfe_price:
                    cursor.mfe_price = lo

    def _apply_corporate_actions(self, ts: int) -> None:
        """Tick phase 2.5: apply any registered corporate actions whose
        ``ts == clock.now_ts`` to the matching open position.

        Cash dividends / special / spinoff-cash credit
        ``amount * quantity`` to :attr:`Portfolio.cash` and emit a
        :class:`CashAdjustment` record. Stock splits rescale the
        position's ``quantity`` AND ``avg_cost`` (by inverse ratio so
        cost basis ``quantity * avg_cost`` is preserved across the
        split boundary) and emit a :class:`QuantityAdjustment`.

        Symbols with no open position at the ex-event are skipped —
        the action is still marked applied so a later open doesn't
        retroactively receive the credit. (Trader expectation: you
        only get the dividend if you held through ex-date.)

        Actions on the same ts are applied in caller-provided order
        (the post-sort list). Multiple actions at the same ts for one
        symbol are rare in practice (e.g. cash div + special div on
        the same date) but supported.
        """
        for sym, queue in self._pending_actions_by_symbol.items():
            for action in queue:
                if action.ts != int(ts):
                    continue
                key = (sym, int(action.ts), action.kind,
                       int(action.ratio_num), int(action.ratio_den),
                       float(action.amount), str(action.source_ref))
                if key in self._applied_action_keys:
                    continue
                self._applied_action_keys.add(key)

                pos = self.portfolio.positions.get(sym)
                if pos is None or pos.quantity == 0.0:
                    continue

                if action.kind in ("cash_dividend", "special_dividend", "spinoff_cash"):
                    reason = action.kind
                    # Only long positions receive a dividend credit.
                    # Shorts pay the dividend (cash flows out). v1 keeps
                    # the symmetry: signed quantity drives the cash flow.
                    cash_flow = float(action.amount) * float(pos.quantity)
                    self.portfolio.cash += cash_flow
                    self.cash_adjustments.append(CashAdjustment(
                        ts=int(action.ts),
                        symbol=sym,
                        amount_per_share=float(action.amount),
                        quantity=float(pos.quantity),
                        reason=reason,
                        source_ref=str(action.source_ref),
                    ))
                elif action.kind == "stock_split":
                    if action.ratio_den == 0:
                        continue
                    ratio = float(action.ratio_num) / float(action.ratio_den)
                    if ratio <= 0.0 or ratio == 1.0:
                        continue
                    pre_qty = float(pos.quantity)
                    pos.quantity = pre_qty * ratio
                    if pre_qty != 0.0:
                        # Inverse-rescale avg_cost so cost basis
                        # quantity * avg_cost stays constant across the
                        # split (consistent with adjusted-bar semantics).
                        pos.avg_cost = pos.avg_cost / ratio
                    # Keep the open-trade cursor's quantity aligned so
                    # MAE/MFE reporting at close uses the post-split
                    # quantity.
                    cursor = self._open_trades.get(sym)
                    if cursor is not None:
                        cursor.quantity = pos.quantity
                        cursor.entry_price = cursor.entry_price / ratio
                    self.quantity_adjustments.append(QuantityAdjustment(
                        ts=int(action.ts),
                        symbol=sym,
                        ratio_num=int(action.ratio_num),
                        ratio_den=int(action.ratio_den),
                        pre_quantity=pre_qty,
                        reason="stock_split",
                        source_ref=str(action.source_ref),
                    ))
                # Unknown kinds are silently ignored — keeps the engine
                # forward-compatible with future event types.

    def _mark_to_market(
        self,
        idx_by_symbol: Mapping[str, int],
        ts: int,
    ) -> None:
        """Append an equity-curve point using this bar's close per symbol."""
        closes = {
            sym: float(self.bars_by_symbol[sym].close[i])
            for sym, i in idx_by_symbol.items()
        }
        self.portfolio.mark_to_market(ts, closes)

    # ---- internal helpers ------------------------------------------------

    def _apply_fill_with_tracking(self, fill: Fill) -> None:
        """Apply a fill to the portfolio AND keep open-trade cursors in sync.

        Routes to one of four cases based on how the fill changes the
        symbol's signed position quantity:

        * ``0 → nonzero``        — opens a fresh cursor.
        * ``same-sign add``      — rolls cursor entry to weighted-avg
                                   (matches Position.avg_cost) so MAE/MFE
                                   continues to track the *new* cost basis
                                   from this bar forward.
        * ``reduce-to-zero``     — closes the cursor, emits a PostTradeReview.
        * ``flip through zero``  — closes the original cursor at fill_price,
                                   then opens a new cursor in the opposite
                                   direction (also at fill_price).
        * ``partial reduce``     — keeps the cursor, drops live qty.
        """
        sym = fill.symbol
        before = self.portfolio.positions.get(sym)
        old_qty = 0.0 if before is None else before.quantity

        self.portfolio.apply_fill(fill)

        new_qty = self.portfolio.positions[sym].quantity
        signed = fill.quantity if fill.side is Side.BUY else -fill.quantity

        if old_qty == 0.0 and new_qty != 0.0:
            self._open_cursor_from_fill(fill, new_qty, link_pre_trade=True)
            return

        cursor = self._open_trades.get(sym)
        if cursor is None:
            return  # defensive — shouldn't happen if old_qty != 0

        same_direction_add = ((old_qty > 0 and signed > 0)
                              or (old_qty < 0 and signed < 0))
        if same_direction_add:
            # Roll entry to weighted-avg cost basis (mirrors Position.avg_cost)
            # so MAE/MFE rolling extrema reset against the new basis. The
            # cursor's *side* is unchanged.
            cursor.entry_price = self.portfolio.positions[sym].avg_cost
            cursor.quantity = new_qty
            return

        # Reducing, closing, or flipping.
        if new_qty == 0.0:
            self._close_cursor(cursor, fill)
        elif (old_qty > 0) != (new_qty > 0):
            # Flipped through zero: close original leg + open opposite leg.
            self._close_cursor(cursor, fill)
            self._open_cursor_from_fill(fill, new_qty, link_pre_trade=False)
        else:
            cursor.quantity = new_qty  # partial reduce

    def _open_cursor_from_fill(
        self,
        fill: Fill,
        new_qty: float,
        *,
        link_pre_trade: bool,
    ) -> None:
        """Create a fresh ``_OpenTradeCursor`` keyed off ``fill``.

        ``link_pre_trade=True`` consumes any matching ``PreTradeEntry``
        from ``_pending_pre_by_order`` so the post-trade review can
        cite the original thesis. Position flips pass ``False`` —
        the new leg has no fresh user-submitted thesis.
        """
        sym = fill.symbol
        ref_id: str | None = None
        if link_pre_trade:
            pre = self._pending_pre_by_order.pop(fill.order_id, None)
            if pre is not None:
                ref_id = pre.order_id
        self._open_trades[sym] = _OpenTradeCursor(
            symbol=sym,
            side="buy" if new_qty > 0 else "sell",
            entry_ts=fill.fill_ts,
            entry_price=fill.fill_price,
            quantity=new_qty,
            mae_price=fill.fill_price,
            mfe_price=fill.fill_price,
            ref_pre_trade_id=ref_id,
        )

    def _close_cursor(self, cursor: _OpenTradeCursor, fill: Fill) -> None:
        """Emit a PostTradeReview for ``cursor`` and drop it from the book."""
        self._build_post_trade(cursor, exit_ts=fill.fill_ts,
                               exit_price=fill.fill_price)
        self._open_trades.pop(cursor.symbol, None)

    def _build_post_trade(
        self,
        cursor: _OpenTradeCursor,
        exit_ts: int,
        exit_price: float,
    ) -> None:
        """Emit a :class:`PostTradeReview` from a closed cursor."""
        qty = abs(cursor.quantity)
        if cursor.side == "buy":
            pnl = (exit_price - cursor.entry_price) * qty
            mae = (cursor.mae_price - cursor.entry_price) * qty   # negative-or-zero
            mfe = (cursor.mfe_price - cursor.entry_price) * qty   # positive-or-zero
            mae_pct = (cursor.mae_price - cursor.entry_price) / cursor.entry_price
            mfe_pct = (cursor.mfe_price - cursor.entry_price) / cursor.entry_price
            pnl_pct = (exit_price - cursor.entry_price) / cursor.entry_price
        else:
            pnl = (cursor.entry_price - exit_price) * qty
            mae = (cursor.entry_price - cursor.mae_price) * qty   # negative-or-zero
            mfe = (cursor.entry_price - cursor.mfe_price) * qty   # positive-or-zero
            mae_pct = (cursor.entry_price - cursor.mae_price) / cursor.entry_price
            mfe_pct = (cursor.entry_price - cursor.mfe_price) / cursor.entry_price
            pnl_pct = (cursor.entry_price - exit_price) / cursor.entry_price

        self.post_trades.append(PostTradeReview(
            symbol=cursor.symbol,
            entry_ts=cursor.entry_ts,
            exit_ts=int(exit_ts),
            entry_price=float(cursor.entry_price),
            exit_price=float(exit_price),
            quantity=float(qty),
            side=cursor.side,
            pnl=float(pnl),
            pnl_pct=float(pnl_pct),
            mae=float(mae),
            mfe=float(mfe),
            mae_pct=float(mae_pct),
            mfe_pct=float(mfe_pct),
            ref_pre_trade_id=cursor.ref_pre_trade_id,
        ))

    def flatten_all_at_close(
        self,
        last_bar_ts: int,
        prices: Mapping[str, float],
        *,
        order_id_prefix: str = "auto-flat",
    ) -> list[Fill]:
        """Synthesise market-close fills for every open position.

        Used by the auto-cycle path (Phase 1d): when the master clock
        reaches end-of-day and the controller is about to roll into
        the next eligible date, any still-open positions need to be
        closed so they don't carry "across" what is conceptually a
        random unrelated trading day. Each position is closed at the
        supplied close price (no slippage, no commission — these are
        synthetic system-flatten fills, not user actions).

        For each non-zero position a :class:`Fill` is appended to
        ``self.fills`` and routed through the same cursor-update
        machinery that handles user-submitted closes, so a
        :class:`PostTradeReview` is emitted with full MAE/MFE and the
        cursor's existing pre-trade link is preserved. Returns the
        newly-synthesised fills (in iteration order) for callers that
        want to surface them in a status message.

        Pending market orders that never filled are silently dropped
        (cancelled) — they were aimed at bars that no longer exist.
        """
        new_fills: list[Fill] = []
        # Drop pending-but-unfilled orders: their target bars are gone.
        if self.pending_orders:
            for o in self.pending_orders:
                self._pending_pre_by_order.pop(o.order_id, None)
            self.pending_orders.clear()

        # Snapshot the position list before mutation: closing one
        # position doesn't remove the dict entry (qty just hits zero),
        # but iterating during mutation is still safer with a copy.
        for sym in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions.get(sym)
            if pos is None or pos.quantity == 0.0:
                continue
            close_px = float(prices.get(sym, pos.avg_cost))
            # Side that flattens: longs sell, shorts buy.
            side = Side.SELL if pos.quantity > 0 else Side.BUY
            qty = abs(float(pos.quantity))
            order_id = f"{order_id_prefix}-{sym}-{int(last_bar_ts)}"
            fill = Fill(
                order_id=order_id,
                symbol=sym,
                side=side,
                quantity=qty,
                fill_price=close_px,
                fill_ts=int(last_bar_ts),
                slippage_bps=0.0,
                commission=0.0,
            )
            self.fills.append(fill)
            self._apply_fill_with_tracking(fill)
            new_fills.append(fill)

        return new_fills

    def result(self) -> SessionResult:
        """Snapshot the engine's current state as a SessionResult."""
        return SessionResult(
            spec=self.spec,
            fills=list(self.fills),
            pre_trades=list(self.pre_trades),
            post_trades=list(self.post_trades),
            equity_curve=list(self.portfolio.equity_curve),
            final_cash=float(self.portfolio.cash),
            cash_adjustments=list(self.cash_adjustments),
            quantity_adjustments=list(self.quantity_adjustments),
        )

    def run_to_completion(self) -> SessionResult:
        """Drive ``tick()`` until exhausted. Used by Phase 2 batch runners
        and by the reproducibility smoke check."""
        while self.tick():
            pass
        return self.result()
