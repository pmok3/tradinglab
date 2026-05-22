"""Live entry-strategy evaluator.

Owns the runtime state machinery for entry strategies — the per-strategy
arm flag, per-(strategy, symbol) cooldown / max-fire counters, the
pending-position-id tracker for on-fill bracket binding, and the
SCANNER_ALERT subscription. Sits between :class:`PositionTracker`
(opens new positions on fill), the bar/tick stream
(:meth:`on_tick`), the optional :class:`BarsRegistry` (for INDICATOR
triggers), the optional :class:`ScanRunner` (for SCANNER_ALERT
triggers), the :class:`EntrySignalSink` (paper / manual sink), the
:class:`RiskGate`, the :class:`AuditLog`, and — once a position is
born — the :class:`ExitEvaluator` for declarative bracket-on-fill.

Responsibilities (per the entries-v1 plan, Layer 3 — evaluator):

1. **Library + arm state.** :meth:`set_strategies` refreshes the loaded
   library (called by the storage-changed callback). :meth:`arm` /
   :meth:`disarm` / :meth:`disarm_all` toggle runtime arm state.
   ``armed`` is RUNTIME-ONLY — wiped on construction (app restart
   wipes arm state per the locked design).

2. **Per-tick evaluation.** :meth:`on_tick` walks every armed
   strategy, resolves its universe to a candidate-symbol set, applies
   gates (cheapest first), evaluates the trigger via :mod:`entries.spec`
   pure helpers, mints a ``pending_position_id``, computes qty via
   :mod:`entries.sizing`, runs the :class:`RiskGate`, and submits an
   :class:`EntrySignal` to the sink. Every step is audited. ``MARKET``
   triggers fire only on ``is_close=True`` bars (next CLOSED bar after
   arm); LIMIT/STOP/STOP_LIMIT use touched-through detection.

3. **Indicator triggers.** Cross-interval condition trees evaluate
   via :func:`scanner.engine.evaluate_group` against an
   :class:`EvaluationContext` built from the :class:`BarsRegistry`
   view for ``(symbol, trigger.interval or default_interval)``.

4. **SCANNER_ALERT triggers.** The evaluator subscribes to
   :class:`ScanRunner` at construction. The ``_on_scan_results``
   callback is invoked on the caller's thread (per the locked Q&A —
   ScanRunner aggregates before fan-out). For each armed strategy with
   ``trigger.kind == SCANNER_ALERT``, look up
   ``results[trigger.scanner_id].new_rows`` and route each row through
   the same fire path used by ``on_tick``.

5. **On-fill bracket chain.** The evaluator subscribes to
   :class:`PositionTracker`. When a position OPENs whose id is in the
   ``_pending_position_ids`` map, look up the originating strategy and
   try to attach each entry in ``strategy.on_fill_exit_ids`` via the
   ``ExitEvaluator``. Missing exit-strategy ids are logged via
   ``entry_bind_failed`` audit. If ``on_fill_exit_ids`` is empty, emit
   a ``request_attach_modal`` signal so the GUI can prompt the user
   (mirrors the exits-v1 N5 modal).

6. **Lifecycle gates.** Block-on-existing-position (per-strategy),
   cooldown, max_fires_per_session_per_symbol, max_fires_per_session_total,
   arm window. All audited via ``entry_blocked`` / ``entry_cooldown`` /
   ``entry_dedup_skipped`` so the user sees WHY a strategy didn't fire.

7. **Panic propagation.** :meth:`disarm_all` is callable from the
   exits panic-flatten code path so a single click clears every armed
   entry strategy too.

Threading
---------

Every public mutator is guarded by ``@require_tk_thread``. Read-only
queries (``armed_strategies``, ``stats``) have no thread restriction.
The :class:`ScanRunner` callback fires on the caller's thread (see
``ScanRunner._dispatch_to_subscribers``), so the subscribed callback is
also guarded. The :class:`PositionTracker` subscriber callback likewise
fires on the caller's thread (tracker uses its own re-entrancy queue)
and is guarded too.
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from datetime import time as dtime
from typing import Any

from ..core.risk_gate import AllowAllRiskGate, RiskGate
from ..core.thread_guard import require_tk_thread
from ..exits.model import OrderSide
from ..positions.model import Position, PositionEvent, PositionEventKind
from ..positions.tracker import PositionTracker
from ..scanner.engine import evaluate_group, make_context
from ..scanner.model import MatchEvidence
from .audit import AuditLog
from .model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    PositionAlreadyOpenPolicy,
    TriggerKind,
    validate_strategy,
)
from .signals import (
    EntryOrderKind,
    EntryPaperSink,
    EntrySignal,
    EntrySignalSink,
)
from .sizing import InvalidSizing, compute_qty
from .spec import (
    should_fire_limit,
    should_fire_market,
    should_fire_stop,
    should_fire_stop_limit,
)

LOG = logging.getLogger(__name__)

__all__ = [
    "EntryEvaluator",
    "EvaluatorStats",
]


# Dedup ring buffer size — enough to remember the last 1024 (strategy,
# symbol, bar_ts_ns) tuples; same bar fired twice in a single tick is
# the dominant case we guard against.
_DEDUP_LRU_SIZE = 1024


@dataclass
class EvaluatorStats:
    fires: int = 0
    blocked: int = 0
    cooldowns: int = 0
    dedup_skips: int = 0
    risk_blocks: int = 0
    on_fill_binds: int = 0
    on_fill_bind_failures: int = 0
    indicator_evaluations: int = 0
    errors: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bar_ts_ns(bar: Any) -> int | None:
    """Best-effort fingerprint for the bar's date/timestamp."""
    ts = getattr(bar, "date", None) or getattr(bar, "timestamp", None)
    if ts is None:
        return None
    try:
        return int(ts.timestamp() * 1_000_000_000)
    except (OSError, ValueError, AttributeError):
        return None


def _parse_hhmm(s: str) -> dtime | None:
    try:
        h, m = s.split(":")
        return dtime(int(h), int(m))
    except Exception:
        return None


class _ArmModalRequest:
    """Sentinel marker used in audit metadata when no on_fill_exit_ids."""


class EntryEvaluator:
    """Live entry-strategy evaluator.

    Construct once at app boot. Holds onto subscriptions to the tracker
    (for OPEN events → bracket-on-fill) and to the scan runner (for
    SCANNER_ALERT triggers).
    """

    def __init__(
        self,
        *,
        tracker: PositionTracker,
        sink: EntrySignalSink,
        audit: AuditLog | None = None,
        risk_gate: RiskGate | None = None,
        bars_registry: Any | None = None,
        scan_runner: Any | None = None,
        exit_evaluator: Any | None = None,
        exit_storage: Any | None = None,
        get_active_symbol: Callable[[], str | None] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        default_interval: str = "1m",
        session_close_time: dtime = dtime(16, 0),
    ) -> None:
        self._tracker = tracker
        self._sink = sink
        self._audit = audit
        self._risk_gate: RiskGate = risk_gate or AllowAllRiskGate()
        self._bars_registry = bars_registry
        self._scan_runner = scan_runner
        self._exit_evaluator = exit_evaluator
        self._exit_storage = exit_storage
        self._get_active_symbol = get_active_symbol or (lambda: None)
        self._clock = clock
        self._default_interval = default_interval
        self._session_close_time = session_close_time

        # Library + arm state.
        self._strategies: dict[str, EntryStrategy] = {}
        self._armed: set[str] = set()

        # Per-(strategy, symbol) counters.
        self._last_fire_ts_per_pair: dict[tuple[str, str], datetime] = {}
        self._fires_per_pair_today: dict[tuple[str, str], int] = {}
        self._fires_per_strategy_today: dict[str, int] = {}
        self._counter_day: date | None = None

        # Pending-position-id → strategy_id (for on-fill bracket bind).
        self._pending_position_ids: dict[str, str] = {}

        # Pending-position-id → paper sink order id (for on_fill housekeeping).
        self._pending_to_order_id: dict[str, str] = {}

        # Dedup ring of (strategy_id, symbol, bar_ts_ns).
        self._dedup_lru: deque = deque(maxlen=_DEDUP_LRU_SIZE)

        # Modal-request callbacks (GUI subscribes; called when an
        # on_fill_exit_ids-empty strategy fills and the user must pick
        # a bracket exit interactively).
        self._modal_subscribers: list[
            Callable[[str, EntryStrategy], None]
        ] = []

        self._stats = EvaluatorStats()

        # Subscriptions.
        self._unsubscribe_tracker = tracker.subscribe(self._on_position_event)
        self._unsubscribe_scanner: Callable[[], None] | None = None
        if scan_runner is not None and hasattr(scan_runner, "subscribe"):
            try:
                self._unsubscribe_scanner = scan_runner.subscribe(
                    self._on_scan_results
                )
            except Exception:  # pragma: no cover - defensive
                LOG.exception("EntryEvaluator: scan_runner.subscribe raised")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Unsubscribe from tracker + scanner. Idempotent."""
        if self._unsubscribe_tracker is not None:
            try:
                self._unsubscribe_tracker()
            except Exception:  # pragma: no cover
                pass
            self._unsubscribe_tracker = None
        if self._unsubscribe_scanner is not None:
            try:
                self._unsubscribe_scanner()
            except Exception:  # pragma: no cover
                pass
            self._unsubscribe_scanner = None

    # ------------------------------------------------------------------
    # Library + arm state
    # ------------------------------------------------------------------

    @require_tk_thread
    def set_strategies(self, strategies: Iterable[EntryStrategy]) -> None:
        """Replace the loaded strategy library.

        Strategies that disappear from the library are also removed
        from the armed set. Strategies that remain keep their armed
        state. New strategies start unarmed.
        """
        new_lib = {s.id: s for s in strategies}
        self._strategies = new_lib
        self._armed = {sid for sid in self._armed if sid in new_lib}

    def get_strategy(self, strategy_id: str) -> EntryStrategy | None:
        return self._strategies.get(strategy_id)

    def all_strategies(self) -> list[EntryStrategy]:
        return list(self._strategies.values())

    def is_armed(self, strategy_id: str) -> bool:
        return strategy_id in self._armed

    def armed_strategies(self) -> set[str]:
        return set(self._armed)

    @require_tk_thread
    def arm(self, strategy_id: str) -> None:
        """Arm a strategy. Refuses to arm invalid or disabled strategies.

        ``validate_strategy`` is run AT ARM TIME (the GUI's permissive
        mid-edit state must not bleed into runtime). Refuses with
        ``ValueError`` if the strategy is broken; auditing is the
        caller's responsibility to surface the error to the user.
        """
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise KeyError(f"unknown entry strategy {strategy_id!r}")
        if not strategy.enabled:
            raise ValueError(
                f"entry strategy {strategy_id!r} is disabled; cannot arm"
            )
        errors = validate_strategy(strategy)
        if errors:
            raise ValueError(
                f"entry strategy {strategy_id!r} failed validation: "
                + "; ".join(errors)
            )
        self._armed.add(strategy_id)
        if self._audit is not None:
            self._audit.append(
                "entry_arm",
                strategy_id=strategy_id,
                meta={"name": strategy.name},
            )

    @require_tk_thread
    def disarm(self, strategy_id: str) -> None:
        if strategy_id in self._armed:
            self._armed.discard(strategy_id)
            if self._audit is not None:
                self._audit.append("entry_disarm", strategy_id=strategy_id)

    @require_tk_thread
    def disarm_all(self) -> None:
        ids = list(self._armed)
        self._armed.clear()
        if self._audit is not None and ids:
            self._audit.append(
                "entry_disarm_all",
                meta={"strategy_ids": ids, "count": len(ids)},
            )

    @require_tk_thread
    def reset_session(self) -> None:
        """Clear per-session counters. Called on chart session reset.

        Does NOT clear armed state — user must explicitly disarm. Does
        clear cooldown timers, max-fire counters, and the dedup ring.
        """
        self._last_fire_ts_per_pair.clear()
        self._fires_per_pair_today.clear()
        self._fires_per_strategy_today.clear()
        self._counter_day = None
        self._dedup_lru.clear()

    # ------------------------------------------------------------------
    # Modal-request subscription (GUI hook)
    # ------------------------------------------------------------------

    def subscribe_modal_request(
        self, callback: Callable[[str, EntryStrategy], None]
    ) -> Callable[[], None]:
        """Subscribe to ``request_attach_modal`` events.

        Fires when a strategy with empty ``on_fill_exit_ids`` fills —
        the GUI is expected to open a modal and let the user pick an
        exit strategy to attach to the new position. Callback signature:
        ``(position_id, entry_strategy)``.
        """
        self._modal_subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._modal_subscribers:
                self._modal_subscribers.remove(callback)

        return _unsubscribe

    # ------------------------------------------------------------------
    # Read-only queries
    # ------------------------------------------------------------------

    def stats(self) -> EvaluatorStats:
        return EvaluatorStats(
            fires=self._stats.fires,
            blocked=self._stats.blocked,
            cooldowns=self._stats.cooldowns,
            dedup_skips=self._stats.dedup_skips,
            risk_blocks=self._stats.risk_blocks,
            on_fill_binds=self._stats.on_fill_binds,
            on_fill_bind_failures=self._stats.on_fill_bind_failures,
            indicator_evaluations=self._stats.indicator_evaluations,
            errors=self._stats.errors,
        )

    def pending_position_ids(self) -> dict[str, str]:
        return dict(self._pending_position_ids)

    # ------------------------------------------------------------------
    # Tick path
    # ------------------------------------------------------------------

    @require_tk_thread
    def on_tick(
        self,
        bars_by_symbol: dict[str, Any],
        ts: datetime,
        *,
        last_bar_forming: bool = False,
    ) -> list[EntrySignal]:
        """Evaluate every armed strategy against the supplied bars.

        ``bars_by_symbol`` maps symbol → latest bar (Bar / Candle /
        BarLike). ``ts`` is the bar timestamp (used for arm-window
        gating + cooldown). ``last_bar_forming`` mirrors the live-tick
        invariant: when True, MARKET triggers do not fire (they require
        a CLOSED bar after arm) and STOP_LIMIT/INDICATOR-on-close
        triggers similarly defer. Sandbox replay always passes
        ``last_bar_forming=False`` (each next_bar IS a closed bar).

        Returns the list of fired signals (after sink submission) so
        callers can drive overlays / logs.
        """
        self._roll_session_counters_if_needed(ts)
        is_close = not last_bar_forming
        fired: list[EntrySignal] = []

        # Capture armed snapshot — modifications during fire (e.g. via
        # subscriber side effects) should not break iteration.
        for strategy_id in list(self._armed):
            strategy = self._strategies.get(strategy_id)
            if strategy is None:
                self._armed.discard(strategy_id)
                continue
            kind = strategy.trigger.kind
            if kind == TriggerKind.SCANNER_ALERT:
                # SCANNER_ALERT triggers fire from the scan-results
                # subscription path, not the tick path.
                continue

            symbols = self._resolve_universe_symbols(strategy)
            if not symbols:
                continue

            # Arm window gate — skip everything if outside window.
            if not self._within_arm_window(strategy, ts):
                continue

            for symbol in symbols:
                bar = bars_by_symbol.get(symbol)
                if bar is None:
                    bar = bars_by_symbol.get(symbol.upper())
                if bar is None:
                    continue

                signal = self._try_fire(
                    strategy=strategy,
                    symbol=symbol,
                    bar=bar,
                    ts=ts,
                    is_close=is_close,
                )
                if signal is not None:
                    fired.append(signal)

        return fired

    # ------------------------------------------------------------------
    # Scanner-alert path
    # ------------------------------------------------------------------

    @require_tk_thread
    def _on_scan_results(self, results: dict[str, Any]) -> None:
        """ScanRunner subscriber. Routes new_rows → SCANNER_ALERT fire path.

        ``results`` is ``Dict[scan_id, ScanResult]`` (per
        ``scanner.runner.ScanRunner.run`` return shape). Fires on the
        caller's thread by contract.
        """
        if not self._armed or not results:
            return
        ts = self._clock()
        self._roll_session_counters_if_needed(ts)

        for strategy_id in list(self._armed):
            strategy = self._strategies.get(strategy_id)
            if strategy is None or strategy.trigger.kind != TriggerKind.SCANNER_ALERT:
                continue
            scanner_id = strategy.trigger.scanner_id
            if not scanner_id:
                continue
            res = results.get(scanner_id)
            if res is None:
                continue
            new_rows = getattr(res, "new_rows", ())
            if not new_rows:
                continue
            if not self._within_arm_window(strategy, ts):
                continue

            universe_symbols = self._resolve_universe_symbols(strategy)
            for row in new_rows:
                row_sym = (getattr(row, "symbol", None) or "").upper()
                if not row_sym:
                    continue
                # Universe filter.
                if (
                    universe_symbols is not None
                    and row_sym not in universe_symbols
                ):
                    continue

                # SCANNER_ALERT triggers don't have a bar reference;
                # synthesise a lightweight bar-like for fill-price
                # selection (the close is the only field needed for
                # MARKET-style fill).
                bar_close = self._extract_row_close(row)
                bar = _ScannerBar(close=bar_close, ts=ts)

                self._try_fire(
                    strategy=strategy,
                    symbol=row_sym,
                    bar=bar,
                    ts=ts,
                    is_close=True,
                    scanner_row=row,
                )

    @staticmethod
    def _extract_row_close(row: Any) -> float:
        """Best-effort close-price extraction from a scanner row."""
        # ScanResult.MatchRow has a `metrics` dict; a "close" key is
        # canonical when the scan exposes one, else fallback to 0.0
        # (the evaluator only uses close for MARKET fill price; sizing
        # uses the same value, so 0.0 will trip the InvalidSizing guard
        # for FIXED_NOTIONAL — which is the correct response).
        metrics = getattr(row, "metrics", None) or {}
        for key in ("close", "Close", "1m close", "1m Close"):
            v = metrics.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        # Fallback: try `close` attribute on the row itself.
        try:
            return float(getattr(row, "close", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------
    # Fire path (shared by tick + scanner)
    # ------------------------------------------------------------------

    def _try_fire(
        self,
        *,
        strategy: EntryStrategy,
        symbol: str,
        bar: Any,
        ts: datetime,
        is_close: bool,
        scanner_row: Any = None,
    ) -> EntrySignal | None:
        symbol = symbol.upper()
        kind = strategy.trigger.kind
        pair = (strategy.id, symbol)

        # Gate 1: position-already-open policy.
        if strategy.position_already_open_policy == PositionAlreadyOpenPolicy.BLOCK:
            if self._has_existing_open_position(strategy.id, symbol):
                self._stats.blocked += 1
                if self._audit is not None:
                    self._audit.append(
                        "entry_blocked",
                        strategy_id=strategy.id,
                        symbol=symbol,
                        meta={"reason": "position_already_open"},
                    )
                return None

        # Gate 2: cooldown.
        if strategy.cooldown_secs > 0:
            last = self._last_fire_ts_per_pair.get(pair)
            if last is not None:
                elapsed = (ts - last).total_seconds()
                if elapsed < strategy.cooldown_secs:
                    self._stats.cooldowns += 1
                    if self._audit is not None:
                        self._audit.append(
                            "entry_cooldown",
                            strategy_id=strategy.id,
                            symbol=symbol,
                            meta={
                                "elapsed_secs": elapsed,
                                "cooldown_secs": strategy.cooldown_secs,
                            },
                        )
                    return None

        # Gate 3: max-fires per pair / total.
        max_pair = strategy.max_fires_per_session_per_symbol
        if max_pair is not None and max_pair > 0:
            if self._fires_per_pair_today.get(pair, 0) >= max_pair:
                self._stats.blocked += 1
                if self._audit is not None:
                    self._audit.append(
                        "entry_blocked",
                        strategy_id=strategy.id,
                        symbol=symbol,
                        meta={
                            "reason": "max_fires_per_symbol",
                            "max": max_pair,
                        },
                    )
                return None
        max_total = strategy.max_fires_per_session_total
        if max_total is not None and max_total > 0:
            if self._fires_per_strategy_today.get(strategy.id, 0) >= max_total:
                self._stats.blocked += 1
                if self._audit is not None:
                    self._audit.append(
                        "entry_blocked",
                        strategy_id=strategy.id,
                        symbol=symbol,
                        meta={
                            "reason": "max_fires_per_session_total",
                            "max": max_total,
                        },
                    )
                return None

        # Gate 4: dedup against same bar fire.
        ts_ns = _bar_ts_ns(bar)
        if ts_ns is not None:
            dedup_key = (strategy.id, symbol, ts_ns)
            if dedup_key in self._dedup_lru:
                self._stats.dedup_skips += 1
                if self._audit is not None:
                    self._audit.append(
                        "entry_dedup_skipped",
                        strategy_id=strategy.id,
                        symbol=symbol,
                        meta={"bar_ts_ns": ts_ns},
                    )
                return None

        # Gate 5: trigger evaluation.
        try:
            fired, fired_evidence = self._evaluate_trigger(
                strategy=strategy,
                symbol=symbol,
                bar=bar,
                is_close=is_close,
                scanner_row=scanner_row,
            )
        except Exception:
            self._stats.errors += 1
            LOG.exception(
                "EntryEvaluator: trigger evaluation raised for %s/%s",
                strategy.id, symbol,
            )
            if self._audit is not None:
                self._audit.append(
                    "entry_broken_strategy_load",
                    strategy_id=strategy.id,
                    symbol=symbol,
                    meta={"reason": "trigger_exception"},
                )
            return None
        if not fired:
            return None

        # Resolve fill side + price + qty.
        direction = strategy.direction
        side = OrderSide.BUY if direction == Direction.LONG else OrderSide.SELL
        position_side = "long" if direction == Direction.LONG else "short"

        ref_price = self._reference_price(strategy.trigger, bar)
        if ref_price is None or ref_price <= 0:
            self._stats.errors += 1
            if self._audit is not None:
                self._audit.append(
                    "entry_blocked",
                    strategy_id=strategy.id,
                    symbol=symbol,
                    meta={"reason": "no_reference_price"},
                )
            return None

        try:
            qty = compute_qty(strategy.sizing, ref_price=ref_price)
        except InvalidSizing as exc:
            self._stats.errors += 1
            if self._audit is not None:
                self._audit.append(
                    "entry_blocked",
                    strategy_id=strategy.id,
                    symbol=symbol,
                    meta={"reason": "invalid_sizing", "detail": str(exc)},
                )
            return None
        if qty <= 0:
            return None

        # Mint pending_position_id.
        pending_id = uuid.uuid4().hex

        order_kind, signal_price, signal_limit = self._signal_price_for_kind(
            kind, strategy.trigger, bar
        )

        # Per-signal extras: ref price + look-back evidence (when the
        # trigger walked a within-last-N-bars window). Evidence is
        # serialized to plain dicts so the signal stays JSON-safe for
        # audit trails. ``MatchEvidence`` is a frozen dataclass with
        # node_id / bars_ago / timestamp / value.
        extras: dict[str, Any] = {"ref_price": float(ref_price)}
        if fired_evidence:
            extras["evidence"] = [
                {
                    "node_id": ev.node_id,
                    "bars_ago": int(ev.bars_ago),
                    "timestamp": ev.timestamp,
                    "value": ev.value,
                }
                for ev in fired_evidence
            ]

        signal = EntrySignal.new(
            strategy_id=strategy.id,
            pending_position_id=pending_id,
            symbol=symbol,
            trigger_id=strategy.trigger.id,
            kind=order_kind,
            side=side,
            position_side=position_side,
            qty=qty,
            price=signal_price,
            limit_price=signal_limit,
            on_fill_exit_ids=tuple(strategy.on_fill_exit_ids),
            label=strategy.name,
            extra=extras,
        )

        # Risk gate (post-signal, pre-submit).
        block = self._risk_gate.check(
            signal, tracker=self._tracker, clock=self._clock
        )
        if block is not None:
            self._stats.risk_blocks += 1
            if self._audit is not None:
                self._audit.append(
                    "entry_blocked",
                    strategy_id=strategy.id,
                    symbol=symbol,
                    meta={
                        "reason": "risk_gate",
                        "gate": block.gate,
                        "detail": block.reason,
                    },
                )
            return None

        # Audit FIRE before submit (so even if submit raises we have a trail).
        if self._audit is not None:
            fire_meta: dict[str, Any] = {
                "kind": order_kind.value,
                "pending_position_id": pending_id,
                "ref_price": ref_price,
            }
            if fired_evidence:
                fire_meta["evidence"] = [
                    {
                        "node_id": ev.node_id,
                        "bars_ago": int(ev.bars_ago),
                        "timestamp": ev.timestamp,
                        "value": ev.value,
                    }
                    for ev in fired_evidence
                ]
            self._audit.append(
                "entry_fire",
                strategy_id=strategy.id,
                symbol=symbol,
                trigger_id=strategy.trigger.id,
                qty=qty,
                price=signal_price,
                meta=fire_meta,
            )

        # Submit via sink.
        try:
            order_id = self._sink.submit(signal)
        except Exception as exc:
            self._stats.errors += 1
            LOG.exception(
                "EntryEvaluator: sink.submit raised for %s/%s",
                strategy.id, symbol,
            )
            if self._audit is not None:
                self._audit.append(
                    "entry_blocked",
                    strategy_id=strategy.id,
                    symbol=symbol,
                    meta={"reason": "submit_exception", "detail": repr(exc)},
                )
            return None

        # Update bookkeeping.
        self._pending_position_ids[pending_id] = strategy.id
        self._pending_to_order_id[pending_id] = order_id
        self._last_fire_ts_per_pair[pair] = ts
        self._fires_per_pair_today[pair] = self._fires_per_pair_today.get(pair, 0) + 1
        self._fires_per_strategy_today[strategy.id] = (
            self._fires_per_strategy_today.get(strategy.id, 0) + 1
        )
        if ts_ns is not None:
            self._dedup_lru.append((strategy.id, symbol, ts_ns))
        self._stats.fires += 1

        if self._audit is not None:
            self._audit.append(
                "entry_submit",
                strategy_id=strategy.id,
                symbol=symbol,
                trigger_id=strategy.trigger.id,
                order_id=order_id,
                meta={
                    "pending_position_id": pending_id,
                    "kind": order_kind.value,
                },
            )

        return signal

    # ------------------------------------------------------------------
    # Trigger evaluation
    # ------------------------------------------------------------------

    def _evaluate_trigger(
        self,
        *,
        strategy: EntryStrategy,
        symbol: str,
        bar: Any,
        is_close: bool,
        scanner_row: Any = None,
    ) -> tuple[bool, list[MatchEvidence]]:
        """Evaluate the trigger; return ``(fired, evidence_list)``.

        ``evidence_list`` is empty for non-INDICATOR triggers and for
        INDICATOR triggers without a within-last-N-bars look-back walk.
        Populated only when the engine's walk fires; downstream
        :class:`EntrySignal` payloads stash it on
        ``signal.extra["evidence"]`` so audit panes and replay overlays
        can render "EMA cross fired 1 bar ago at 10:35" lines.
        """
        kind = strategy.trigger.kind
        direction = strategy.direction
        trig = strategy.trigger

        if kind == TriggerKind.MARKET:
            return should_fire_market(trig, bar, is_close=is_close), []
        if kind == TriggerKind.LIMIT:
            return should_fire_limit(trig, bar, direction=direction), []
        if kind == TriggerKind.STOP:
            return should_fire_stop(trig, bar, direction=direction), []
        if kind == TriggerKind.STOP_LIMIT:
            return should_fire_stop_limit(trig, bar, direction=direction), []
        if kind == TriggerKind.SCANNER_ALERT:
            # Reached only via _on_scan_results, where the row presence
            # implies the trigger fired. The is_close check is enforced
            # by the scanner only emitting on closed bars. The scanner
            # itself owns the look-back evidence; surface it through
            # the row payload (Phase 6) rather than duplicating walk
            # work here.
            row_evidence = list(getattr(scanner_row, "evidence", []) or [])
            return scanner_row is not None, row_evidence
        if kind == TriggerKind.INDICATOR:
            return self._evaluate_indicator(strategy, symbol, bar, is_close)
        return False, []

    def _evaluate_indicator(
        self,
        strategy: EntryStrategy,
        symbol: str,
        bar: Any,
        is_close: bool,
    ) -> tuple[bool, list[MatchEvidence]]:
        if not is_close:
            return False, []
        if self._bars_registry is None:
            return False, []
        condition = strategy.trigger.condition
        if condition is None:
            return False, []
        interval = strategy.trigger.interval or self._default_interval
        try:
            view = self._bars_registry.get_view(symbol, interval)
        except Exception:
            self._stats.errors += 1
            LOG.exception(
                "EntryEvaluator: bars_registry.get_view raised for %s/%s",
                symbol, interval,
            )
            return False, []
        if view is None:
            return False, []
        candles = view.memo.candles if hasattr(view, "memo") else None
        if not candles:
            return False, []
        try:
            ctx = make_context(
                symbol=symbol,
                interval=interval,
                candles=candles,
                memo=view.memo,
                bars=getattr(view, "bars", None),
                bars_registry=self._bars_registry,
            )
            self._stats.indicator_evaluations += 1
            result = evaluate_group(condition, ctx)
            evidence = list(ctx.evidence)
        except NotImplementedError:
            return False, []
        except Exception:
            self._stats.errors += 1
            LOG.exception(
                "EntryEvaluator: indicator evaluation raised for %s",
                strategy.id,
            )
            return False, []
        return (result is True), evidence

    # ------------------------------------------------------------------
    # Universe / arm window helpers
    # ------------------------------------------------------------------

    def _resolve_universe_symbols(
        self, strategy: EntryStrategy
    ) -> set[str] | None:
        """Return uppercase symbol set or None for "any symbol".

        - ``symbols``: explicit set.
        - ``from_attached_chart``: single-element set from
          ``get_active_symbol`` callback (or empty set when no chart).
        - ``scanner_id``: returns ``None`` (any symbol from the scanner).
          For non-SCANNER_ALERT triggers in this universe mode, the
          tick path treats ``None`` as "no candidate symbols" and skips.
        """
        u = strategy.universe
        if u.symbols:
            return {s.upper() for s in u.symbols}
        if u.from_attached_chart:
            sym = self._get_active_symbol()
            return {sym.upper()} if sym else set()
        if u.scanner_id:
            # Tick path can't resolve; SCANNER_ALERT path returns None
            # to mean "any" — caller filters separately.
            if strategy.trigger.kind == TriggerKind.SCANNER_ALERT:
                return None
            return set()
        return set()

    def _within_arm_window(self, strategy: EntryStrategy, ts: datetime) -> bool:
        start = _parse_hhmm(strategy.arm_window_start)
        end = _parse_hhmm(strategy.arm_window_end)
        if start is None or end is None:
            return True
        local_t = ts.timetz().replace(tzinfo=None) if ts.tzinfo else ts.time()
        if start <= end:
            return start <= local_t <= end
        # Window wraps midnight (rare; keep behavior intuitive).
        return local_t >= start or local_t <= end

    def _has_existing_open_position(self, strategy_id: str, symbol: str) -> bool:
        # Block-on-existing semantics: per-strategy. A strategy doesn't
        # block another strategy's positions on the same symbol.
        for pos in self._tracker.list_open():
            if pos.symbol == symbol and pos.strategy_id == strategy_id:
                return True
        return False

    def _roll_session_counters_if_needed(self, ts: datetime) -> None:
        # Counters reset on UTC date roll. Real session-day boundaries
        # would use ET trading day; v1 uses UTC-day for simplicity and
        # correctness in sandbox replays where ``ts`` is the bar time.
        d = ts.date()
        if self._counter_day != d:
            self._fires_per_pair_today.clear()
            self._fires_per_strategy_today.clear()
            self._counter_day = d

    # ------------------------------------------------------------------
    # Reference / signal-price helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _reference_price(trigger: EntryTrigger, bar: Any) -> float | None:
        """Pick the price used for sizing + risk gate."""
        # MARKET / INDICATOR / SCANNER_ALERT: bar.close.
        # LIMIT / STOP_LIMIT: trigger.price (limit price).
        # STOP: trigger.stop_price.
        kind = trigger.kind
        try:
            if kind == TriggerKind.LIMIT:
                return float(trigger.price) if trigger.price else None
            if kind == TriggerKind.STOP_LIMIT:
                return float(trigger.price) if trigger.price else None
            if kind == TriggerKind.STOP:
                return (
                    float(trigger.stop_price) if trigger.stop_price else None
                )
            return float(getattr(bar, "close", 0.0))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _signal_price_for_kind(
        kind: TriggerKind, trigger: EntryTrigger, bar: Any,
    ) -> tuple[EntryOrderKind, float | None, float | None]:
        """Return (order_kind, signal.price, signal.limit_price)."""
        if kind == TriggerKind.LIMIT:
            return (EntryOrderKind.LIMIT, trigger.price, None)
        if kind == TriggerKind.STOP:
            return (EntryOrderKind.STOP, trigger.stop_price, None)
        if kind == TriggerKind.STOP_LIMIT:
            return (
                EntryOrderKind.STOP_LIMIT,
                trigger.stop_price,
                trigger.price,
            )
        # MARKET, INDICATOR, SCANNER_ALERT all collapse to MARKET on the
        # paper engine — no working price (engine fills at bar.close).
        return (EntryOrderKind.MARKET, None, None)

    # ------------------------------------------------------------------
    # Tracker subscriber → on-fill bracket bind
    # ------------------------------------------------------------------

    @require_tk_thread
    def _on_position_event(
        self, event: PositionEvent, position: Position,
    ) -> None:
        if event.kind != PositionEventKind.OPEN:
            return
        pid = position.id
        if pid not in self._pending_position_ids:
            return
        strategy_id = self._pending_position_ids.pop(pid)
        order_id = self._pending_to_order_id.pop(pid, None)

        # Notify sink that the order has filled (so it can drop local
        # indexes — the engine already purged its own).
        if order_id and isinstance(self._sink, EntryPaperSink):
            try:
                self._sink.on_fill(order_id)
            except Exception:  # pragma: no cover
                LOG.exception("EntryEvaluator: sink.on_fill raised")

        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            # Strategy was removed between fire and fill — log and bail.
            if self._audit is not None:
                self._audit.append(
                    "entry_bind_failed",
                    strategy_id=strategy_id,
                    symbol=position.symbol,
                    position_id=pid,
                    meta={"reason": "strategy_disappeared"},
                )
            self._stats.on_fill_bind_failures += 1
            return

        if self._audit is not None:
            self._audit.append(
                "entry_fill",
                strategy_id=strategy_id,
                symbol=position.symbol,
                position_id=pid,
                qty=position.qty_initial,
                price=position.avg_entry_price,
                meta={"side": position.side},
            )

        exit_ids = tuple(strategy.on_fill_exit_ids)
        if not exit_ids:
            # Empty list → emit modal request so GUI can prompt user.
            for cb in list(self._modal_subscribers):
                try:
                    cb(pid, strategy)
                except Exception:  # pragma: no cover - subscriber bug
                    LOG.exception(
                        "EntryEvaluator: modal-request subscriber raised"
                    )
            if self._audit is not None:
                self._audit.append(
                    "entry_modal_requested",
                    strategy_id=strategy_id,
                    symbol=position.symbol,
                    position_id=pid,
                )
            return

        # Bind each exit strategy.
        if self._exit_evaluator is None or self._exit_storage is None:
            if self._audit is not None:
                self._audit.append(
                    "entry_bind_failed",
                    strategy_id=strategy_id,
                    symbol=position.symbol,
                    position_id=pid,
                    meta={
                        "reason": "exit_evaluator_or_storage_missing",
                        "exit_strategy_ids": list(exit_ids),
                    },
                )
            self._stats.on_fill_bind_failures += 1
            return

        for ex_id in exit_ids:
            try:
                ex_strategy = self._exit_storage.load(ex_id)
            except Exception:
                ex_strategy = None
            if ex_strategy is None:
                self._stats.on_fill_bind_failures += 1
                if self._audit is not None:
                    self._audit.append(
                        "entry_bind_failed",
                        strategy_id=strategy_id,
                        symbol=position.symbol,
                        position_id=pid,
                        meta={
                            "reason": "exit_strategy_missing",
                            "exit_strategy_id": ex_id,
                        },
                    )
                continue
            try:
                self._exit_evaluator.attach_strategy(pid, ex_strategy)
                self._stats.on_fill_binds += 1
            except Exception as exc:
                self._stats.on_fill_bind_failures += 1
                LOG.exception(
                    "EntryEvaluator: exit_evaluator.attach_strategy raised "
                    "for %s/%s", pid, ex_id,
                )
                if self._audit is not None:
                    self._audit.append(
                        "entry_bind_failed",
                        strategy_id=strategy_id,
                        symbol=position.symbol,
                        position_id=pid,
                        meta={
                            "reason": "attach_exception",
                            "exit_strategy_id": ex_id,
                            "detail": repr(exc),
                        },
                    )


@dataclass
class _ScannerBar:
    """Lightweight bar-like for SCANNER_ALERT fire path."""

    close: float = 0.0
    ts: datetime | None = None

    @property
    def date(self) -> datetime | None:
        return self.ts

    @property
    def open(self) -> float:
        return self.close

    @property
    def high(self) -> float:
        return self.close

    @property
    def low(self) -> float:
        return self.close
