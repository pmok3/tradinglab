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

Supported trigger kinds (registry-based dispatch via
``_ENTRY_HANDLERS`` / ``_EXIT_HANDLERS``):

* Entry MARKET, LIMIT, STOP, STOP_LIMIT, INDICATOR, SCANNER_ALERT.
* Exit MARKET, LIMIT, STOP, STOP_LIMIT, INDICATOR, TRAILING_STOP,
  TIME_OF_DAY, CHANDELIER.
* ``eod_kill_switch`` honored as a strategy-level MARKET sweep on
  the last bar.
* Multi-leg OCO interpreted as "first leg to fire wins"; partial-fill
  semantics are deferred.

Statefulness:

* ``TRAILING_STOP`` and ``CHANDELIER`` exits delegate to the pure
  functions in :mod:`exits.spec`, threading a per-trigger
  :class:`exits.spec.TriggerState` via :attr:`EvalContext.trigger_states`.
  States are reset on every position-open transition (entry-anchored
  semantics).
* ``SCANNER_ALERT`` entries are edge-triggered against a loaded
  :class:`scanner.model.ScanDefinition`; bar-0 initialises the
  per-trigger prev-match state and subsequent False→True transitions
  fire. This avoids the "every symbol already matching fires on day 1"
  backtest gotcha.

Registry-based dispatch means a new ``TriggerKind`` automatically
lights up the GUI's "Supported" list when a handler is registered.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python <3.9 fallback
    ZoneInfo = None  # type: ignore[assignment]

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
from ..exits.spec import (
    Bar as _SpecBar,
)
from ..exits.spec import (
    TriggerState as _SpecTriggerState,
)
from ..exits.spec import (
    evaluate_chandelier_stop as _spec_evaluate_chandelier,
)
from ..exits.spec import (
    evaluate_time_of_day as _spec_evaluate_time_of_day,
)
from ..exits.spec import (
    evaluate_trailing_stop as _spec_evaluate_trailing,
)
from ..exits.spec import (
    update_chandelier_state as _spec_update_chandelier,
)
from ..exits.spec import (
    update_trail_state as _spec_update_trail,
)
from ..models import Candle
from ..positions.model import Position as _Position
from ..scanner import storage as _scanner_storage
from ..scanner.engine import (
    EvaluationContext as _ScannerEvalContext,
)
from ..scanner.engine import (
    evaluate_group as _scanner_evaluate_group,
)
from ..scanner.engine import (
    make_context as _scanner_make_context,
)
from ..scanner.model import Condition as _ScannerCondition
from ..scanner.model import FieldRef as _ScannerFieldRef
from ..scanner.model import Group as _ScannerGroup
from ..scanner.model import ScanDefinition as _ScanDefinition
from .model import CostModel

LOG = logging.getLogger(__name__)

__all__ = [
    "evaluate_symbol",
    "UnsupportedTriggerKind",
    "EvalContext",
    "_ENTRY_HANDLERS",
    "_EXIT_HANDLERS",
]


# ---------------------------------------------------------------------------
# Time-zone helpers — strategies' arm_window, require_market_open and
# per-session-day counter resets are all interpreted in US/Eastern time
# (ET), the same convention used by the live evaluator's templates and by
# every "trading day" reference in the codebase. Bar timestamps in
# :class:`BarSeries.ts` are int64 epoch SECONDS in UTC; the conversion
# happens here.
# ---------------------------------------------------------------------------


def _et_zoneinfo():
    """Return ``ZoneInfo('America/New_York')`` or ``None`` on missing tzdata.

    Cached at module load via :func:`functools.lru_cache`-equivalent — the
    ``ZoneInfo`` constructor itself caches identical zone names, so
    repeated calls are O(1).
    """
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001 — missing tzdata, fall through
        return None


_ET = _et_zoneinfo()


def _bar_ts_to_et(ts: int) -> datetime:
    """Convert an int64 UTC epoch-second bar timestamp to an ET datetime.

    Falls back to a fixed UTC-5 offset (EST) if ``zoneinfo`` is missing
    on the host. The fallback is off by 1 hour for ~10% of the year
    (EDT vs EST) but RTH never straddles the ET-date boundary, so the
    session-day reset behaviour stays correct.
    """
    if _ET is not None:
        return datetime.fromtimestamp(ts, tz=_ET)
    # tzdata-less fallback: assume UTC, shift -5h, fake an ET-flavoured
    # tz tag. Datetime arithmetic and comparisons still work.
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(
        timezone(timedelta(hours=-5))
    )


def _parse_hhmm_to_time(s: str | None) -> time | None:
    """Parse an ``"HH:MM"`` string into a :class:`datetime.time`.

    Returns ``None`` for blank/malformed input — mirrors the live
    ``entries.evaluator._parse_hhmm`` semantics so blank arm windows
    disable the gate cleanly.
    """
    if not s:
        return None
    try:
        h, m = s.split(":")
        return time(hour=int(h), minute=int(m))
    except (ValueError, AttributeError):
        return None


def _within_arm_window(strategy: EntryStrategy, et_dt: datetime) -> bool:
    """True iff ``et_dt`` falls within the strategy's arm window.

    Blank arm-window strings (or unparseable values) disable the gate.
    Window may wrap midnight (start > end) — rare for US-equity
    strategies but supported for compatibility with the live
    evaluator.
    """
    start = _parse_hhmm_to_time(strategy.arm_window_start)
    end = _parse_hhmm_to_time(strategy.arm_window_end)
    if start is None or end is None:
        return True
    local_t = et_dt.time()
    if start <= end:
        return start <= local_t <= end
    return local_t >= start or local_t <= end


# Regular-session boundaries for US equities. Live evaluator treats
# RTH = 09:30:00–16:00:00 ET, Mon–Fri (holidays not enforced — the
# strategy tester also runs against arbitrary user-supplied data so we
# leave holiday filtering to the data layer).
_RTH_OPEN = time(hour=9, minute=30)
_RTH_CLOSE = time(hour=16, minute=0)


def _is_regular_session(et_dt: datetime) -> bool:
    """True iff ``et_dt`` is Mon–Fri AND 09:30 ≤ time ≤ 16:00 ET."""
    if et_dt.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    local_t = et_dt.time()
    return _RTH_OPEN <= local_t <= _RTH_CLOSE


def _normalize_intervals(
    node: _ScannerGroup | _ScannerCondition,
    interval: str,
) -> _ScannerGroup | _ScannerCondition:
    """Deep-clone a scanner condition tree with all intervals forced to ``interval``.

    The strategy tester runs in **single-interval mode** in PR-1: the
    test's outer ``interval`` (from ``TestConfig.interval``) is the
    one-and-only data interval. Saved entry/exit strategies authored
    via the entries/exits dialogs typically carry per-Condition
    ``interval="5m"`` and per-FieldRef ``interval=None`` (the model
    defaults). When the user runs the strategy tester at any other
    interval (e.g. ``"1d"``), the scanner's cross-interval gate in
    :func:`scanner.engine.evaluate_condition` silently returns ``None``
    (no ``bars_registry`` is wired in the headless path) and **no
    triggers ever fire** — see GH bug "0 trades on 3/8 EMA cross
    default".

    This helper rewrites the tree so every leaf's interval matches
    the test interval, letting the existing same-interval evaluation
    path do its job. Returns a NEW tree; the input is not mutated.

    True cross-interval evaluation in mechanical testing requires a
    ``BarsRegistry`` populated from multi-interval candle fetches and
    is deferred to a future PR.
    """
    if isinstance(node, _ScannerCondition):
        # Strip FieldRef intervals (set to None) so the field-level
        # cross-interval gate also no-ops.
        new_params: dict = {}
        for k, v in node.params.items():
            if isinstance(v, _ScannerFieldRef) and v.interval is not None:
                if v.kind == "literal":
                    new_params[k] = _ScannerFieldRef(
                        kind=v.kind, value=v.value, interval=None,
                    )
                else:
                    new_params[k] = _ScannerFieldRef(
                        kind=v.kind, id=v.id, params=dict(v.params or {}),
                        output_key=v.output_key, interval=None,
                    )
            else:
                new_params[k] = v
        left = node.left
        if left.interval is not None:
            if left.kind == "literal":
                left = _ScannerFieldRef(
                    kind=left.kind, value=left.value, interval=None,
                )
            else:
                left = _ScannerFieldRef(
                    kind=left.kind, id=left.id,
                    params=dict(left.params or {}),
                    output_key=left.output_key, interval=None,
                )
        return _ScannerCondition(
            id=node.id,
            left=left,
            op=node.op,
            params=new_params,
            interval=interval,
            enabled=node.enabled,
            comment=node.comment,
            within_last_bars=node.within_last_bars,
            within_last_mode=node.within_last_mode,
        )
    # Group
    return _ScannerGroup(
        combinator=node.combinator,
        children=[_normalize_intervals(c, interval) for c in node.children],
        enabled=node.enabled,
        id=node.id,
        within_last_bars=node.within_last_bars,
        within_last_mode=node.within_last_mode,
    )


def _build_normalized_conditions(
    entry_strategy: EntryStrategy,
    exit_strategy: ExitStrategy,
    interval: str,
) -> dict[str, _ScannerGroup]:
    """Build a ``trigger.id -> normalized condition tree`` cache.

    Walked once per ``evaluate_symbol`` call; the indicator and
    scanner-alert handlers look up the normalized condition rather
    than re-walking the tree per bar.

    For ``SCANNER_ALERT`` triggers, this also resolves the
    :attr:`EntryTrigger.scanner_id` to a saved
    :class:`scanner.model.ScanDefinition` via
    :func:`scanner.storage.load` and normalises its
    :attr:`ScanDefinition.root` group. Load failures
    (FileNotFoundError / ValueError) are logged and the trigger is
    skipped — the handler then silently no-fires (mirrors the
    defensive contract used by INDICATOR triggers).
    """
    out: dict[str, _ScannerGroup] = {}
    if (
        entry_strategy.trigger.kind is EntryTriggerKind.INDICATOR
        and entry_strategy.trigger.condition is not None
    ):
        normalized = _normalize_intervals(
            entry_strategy.trigger.condition, interval,
        )
        if isinstance(normalized, _ScannerGroup):
            out[entry_strategy.trigger.id] = normalized
    if (
        entry_strategy.trigger.kind is EntryTriggerKind.SCANNER_ALERT
        and entry_strategy.trigger.scanner_id
    ):
        try:
            scan: _ScanDefinition = _scanner_storage.load(
                entry_strategy.trigger.scanner_id,
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            LOG.warning(
                "strategy_tester._build_normalized_conditions: "
                "could not load scanner_id=%s: %s",
                entry_strategy.trigger.scanner_id, exc,
            )
        else:
            normalized = _normalize_intervals(scan.root, interval)
            if isinstance(normalized, _ScannerGroup):
                out[entry_strategy.trigger.id] = normalized
    for leg in exit_strategy.legs:
        if not leg.enabled:
            continue
        for trig in leg.triggers:
            if not trig.enabled:
                continue
            if trig.kind is not ExitTriggerKind.INDICATOR:
                continue
            if trig.condition is None:
                continue
            normalized = _normalize_intervals(trig.condition, interval)
            if isinstance(normalized, _ScannerGroup):
                out[trig.id] = normalized
    return out


def _walk_authored_intervals(
    node: _ScannerGroup | _ScannerCondition,
) -> list[str]:
    """Yield every per-Condition / per-FieldRef interval string in a tree.

    Used by :func:`collect_interval_overrides` to surface mismatches
    between the strategy's authored intervals and the test's outer
    interval. Returns interval strings ordered by tree traversal;
    duplicates are preserved (deduplication is the caller's job).
    """
    out: list[str] = []
    if isinstance(node, _ScannerCondition):
        if node.interval is not None:
            out.append(str(node.interval))
        if node.left is not None and getattr(node.left, "interval", None):
            out.append(str(node.left.interval))
        for v in (node.params or {}).values():
            iv = getattr(v, "interval", None)
            if iv:
                out.append(str(iv))
        return out
    for child in node.children:
        out.extend(_walk_authored_intervals(child))
    return out


def collect_interval_overrides(
    entry_strategy: EntryStrategy,
    exit_strategy: ExitStrategy,
    test_interval: str,
) -> list[str]:
    """Return one human-readable warning string per overridden interval.

    The strategy tester runs in single-interval mode and rewrites
    every authored ``interval`` to ``test_interval`` (see
    :func:`_normalize_intervals`). When the user's authored interval
    differs, this helper produces a string like::

        "entry trigger '<label-or-id>' authored at 1m; evaluated at 5m"

    Returns an empty list when every authored interval matches
    ``test_interval`` (the no-override happy path) and when the
    strategies don't carry any indicator-style conditions at all
    (e.g. MARKET entry + STOP exit — no condition trees to walk).
    """

    def _label(trig_id: str, trig_label: str | None) -> str:
        if trig_label and str(trig_label).strip():
            return str(trig_label).strip()
        return trig_id

    msgs: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    def _emit(scope: str, label: str, iv: str) -> None:
        if iv == test_interval:
            return
        key = (scope, label, iv)
        if key in seen:
            return
        seen.add(key)
        msgs.append(
            f"{scope} trigger '{label}' authored at {iv}; "
            f"evaluated at {test_interval} (single-interval mode)"
        )

    # Entry trigger interval itself (the trigger object carries one).
    et = entry_strategy.trigger
    if et.kind is EntryTriggerKind.INDICATOR and et.condition is not None:
        if et.interval:
            _emit("entry", _label(et.id, et.label), str(et.interval))
        for iv in _walk_authored_intervals(et.condition):
            _emit("entry", _label(et.id, et.label), iv)
    if et.kind is EntryTriggerKind.SCANNER_ALERT and et.scanner_id:
        try:
            scan: _ScanDefinition = _scanner_storage.load(et.scanner_id)
        except (FileNotFoundError, ValueError, OSError):
            scan = None  # type: ignore[assignment]
        if scan is not None:
            for iv in _walk_authored_intervals(scan.root):
                _emit("entry", _label(et.id, et.label), iv)

    # Exit triggers — each leg's INDICATOR triggers carry condition trees.
    for leg in exit_strategy.legs:
        if not leg.enabled:
            continue
        for trig in leg.triggers:
            if not trig.enabled:
                continue
            if trig.kind is not ExitTriggerKind.INDICATOR:
                continue
            if trig.condition is None:
                continue
            label = _label(trig.id, getattr(trig, "label", None))
            if getattr(trig, "interval", None):
                _emit("exit", label, str(trig.interval))
            for iv in _walk_authored_intervals(trig.condition):
                _emit("exit", label, iv)

    return msgs


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
    # Per-trigger runtime state for stateful exit triggers
    # (TRAILING_STOP, CHANDELIER). Keyed by trigger.id. Reset on every
    # position-open transition (entry-anchored semantics: each new
    # position starts a fresh HWM / chandelier window).
    trigger_states: dict[str, _SpecTriggerState] = field(default_factory=dict)
    # Tracks the previous bar's position_open value so we can detect
    # the "activation bar" — the first bar a position is observed open
    # — to correctly seed chandelier window state and reset trail HWM.
    prev_position_open: bool = False
    # Per-trigger previous match state for SCANNER_ALERT entries.
    # Bar-0 just observes (no fire); subsequent False→True transitions
    # fire (mirrors the live ScanRunner's edge-detection behaviour for
    # backtest, less the "first-match-after-empty-history-is-new"
    # interpretation that would force every already-matching symbol
    # to fire on bar 0).
    scanner_alert_prev_match: dict[str, bool] = field(default_factory=dict)
    # ET trading-day boundary tracking — see ``_roll_session_counters``.
    # ``max_fires_per_session_per_symbol`` and ``max_fires_per_session_total``
    # are interpreted as "per ET trading day". When the ET date rolls
    # (UTC date is too coarse for ET — 14:00 ET on Dec 31 ≠ 14:00 ET on
    # Jan 1 in UTC), ``fires_total`` and ``fires_by_symbol`` are reset
    # to 0 so the next day's first eligible bar can fire again.
    current_session_et_date: date | None = None
    # Timestamp of the most recent successful entry fire — drives the
    # ``cooldown_secs`` gate. ``None`` until the first fire.
    last_fire_ts: int | None = None

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


def _entry_limit(
    trigger: EntryTrigger, bar: _BarTuple, *, side: Side, **_kw: object
) -> bool:
    """LIMIT entry: LONG fires if bar.low <= price; SHORT fires if bar.high >= price."""
    price = trigger.price
    if price is None:
        return False
    _o, hi, lo, _c = bar
    if side is Side.BUY:
        return lo <= float(price)
    return hi >= float(price)


def _entry_stop(
    trigger: EntryTrigger, bar: _BarTuple, *, side: Side, **_kw: object
) -> bool:
    """STOP entry: LONG fires if bar.high >= stop; SHORT fires if bar.low <= stop."""
    stop = trigger.stop_price
    if stop is None:
        return False
    _o, hi, lo, _c = bar
    if side is Side.BUY:
        return hi >= float(stop)
    return lo <= float(stop)


def _entry_stop_limit(
    trigger: EntryTrigger, bar: _BarTuple, *, side: Side, **_kw: object
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


def _entry_indicator(
    trigger: EntryTrigger,
    bar: _BarTuple,
    *,
    eval_ctx: _ScannerEvalContext | None = None,
    normalized_conditions: dict[str, _ScannerGroup] | None = None,
    **_kw: object,
) -> bool:
    """INDICATOR entry: evaluate ``trigger.condition`` at the current bar.

    Uses the same :func:`scanner.engine.evaluate_group` kernel the live
    :class:`EntryEvaluator` uses, so semantics — tri-valued AND/OR,
    within-last-N-bars look-back, transition operators — match exactly.
    The decision is made against ``bar i``'s close (mirrors the
    decide-at-close / fill-next-open contract for the rest of the
    handlers). If the condition is missing or evaluation raises, the
    trigger silently does NOT fire (defensive — a broken indicator
    shouldn't abort the entire Run; the scanner kernel already logs
    indicator errors via ``IndicatorMemo.errors``).

    ``normalized_conditions`` maps ``trigger.id`` → condition tree
    with all per-Condition / per-FieldRef intervals forced to the
    test's outer interval. Built once per symbol by
    :func:`_build_normalized_conditions`. Without it, conditions
    authored at a different default interval (e.g. ``"5m"``) silently
    no-fire against the test's interval (e.g. ``"1d"``) due to the
    scanner's cross-interval gate.
    """
    if eval_ctx is None or trigger.condition is None:
        return False
    condition: _ScannerGroup = trigger.condition  # type: ignore[assignment]
    if normalized_conditions is not None:
        condition = normalized_conditions.get(trigger.id, condition)
    try:
        result = _scanner_evaluate_group(condition, eval_ctx)
    except NotImplementedError:
        # Cross-interval / unimplemented indicator path — treat as "no fire".
        return False
    except Exception:  # noqa: BLE001
        LOG.exception(
            "strategy_tester._entry_indicator: evaluate_group raised "
            "(symbol=%s, idx=%d)", eval_ctx.symbol, eval_ctx.current_index,
        )
        return False
    return result is True


def _entry_scanner_alert(
    trigger: EntryTrigger,
    bar: _BarTuple,
    *,
    eval_ctx: _ScannerEvalContext | None = None,
    normalized_conditions: dict[str, _ScannerGroup] | None = None,
    eval_state: EvalContext | None = None,
    **_kw: object,
) -> bool:
    """SCANNER_ALERT entry: evaluate the referenced Scan per bar with
    edge-trigger semantics (False/None → True transition fires).

    ``trigger.scanner_id`` is resolved via :func:`scanner.storage.load`
    once per symbol and stashed in ``normalized_conditions`` keyed by
    trigger.id. Per-Condition / per-FieldRef intervals on the Scan are
    forced to the test's outer interval (same single-interval-mode
    convention as INDICATOR triggers).

    Bar 0 records the current match into
    :attr:`EvalContext.scanner_alert_prev_match` without firing — this
    avoids the "every symbol already matching fires on day 1" backtest
    gotcha while still letting fresh transitions during the test
    period fire normally.

    Silently no-fire on:

    * missing scanner_id field
    * scan file not found / corrupt
    * scanner_engine raising NotImplementedError (e.g. cross-interval)
    * any other scanner-engine exception (logged)
    """
    if eval_ctx is None or eval_state is None:
        return False
    if not trigger.scanner_id:
        return False
    if normalized_conditions is None:
        return False
    condition = normalized_conditions.get(trigger.id)
    if condition is None:
        # The scan failed to load at evaluate_symbol startup
        # (FileNotFoundError, etc.) — silently no-fire.
        return False
    try:
        result = _scanner_evaluate_group(condition, eval_ctx)
    except NotImplementedError:
        return False
    except Exception:  # noqa: BLE001
        LOG.exception(
            "strategy_tester._entry_scanner_alert: evaluate_group raised "
            "(symbol=%s, idx=%d, scanner_id=%s)",
            eval_ctx.symbol, eval_ctx.current_index, trigger.scanner_id,
        )
        return False
    matched_now = result is True

    prev = eval_state.scanner_alert_prev_match.get(trigger.id)
    eval_state.scanner_alert_prev_match[trigger.id] = matched_now
    if prev is None:
        # Bar 0: just observe, never fire (avoids the "already-matching
        # on day 1 fires unintentionally" trap).
        return False
    return matched_now and not prev


_ENTRY_HANDLERS: dict[EntryTriggerKind, Callable[..., bool]] = {
    EntryTriggerKind.MARKET: _entry_market,
    EntryTriggerKind.LIMIT: _entry_limit,
    EntryTriggerKind.STOP: _entry_stop,
    EntryTriggerKind.STOP_LIMIT: _entry_stop_limit,
    EntryTriggerKind.INDICATOR: _entry_indicator,
    EntryTriggerKind.SCANNER_ALERT: _entry_scanner_alert,
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
    **_kw: object,
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
    **_kw: object,
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
    **_kw: object,
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


def _exit_indicator(
    trigger: ExitTrigger,
    bar: _BarTuple,
    *,
    eval_ctx: _ScannerEvalContext | None = None,
    normalized_conditions: dict[str, _ScannerGroup] | None = None,
    **_kw: object,
) -> bool:
    """INDICATOR exit: evaluate ``trigger.condition`` at the current bar.

    Mirrors :func:`_entry_indicator`. The exit fires when the condition
    evaluates True; defensive about None / NotImplementedError /
    indicator-compute exceptions (no fire on error so a broken
    indicator doesn't flatten the position prematurely).
    """
    if eval_ctx is None or trigger.condition is None:
        return False
    condition: _ScannerGroup = trigger.condition  # type: ignore[assignment]
    if normalized_conditions is not None:
        condition = normalized_conditions.get(trigger.id, condition)
    try:
        result = _scanner_evaluate_group(condition, eval_ctx)
    except NotImplementedError:
        return False
    except Exception:  # noqa: BLE001
        LOG.exception(
            "strategy_tester._exit_indicator: evaluate_group raised "
            "(symbol=%s, idx=%d)", eval_ctx.symbol, eval_ctx.current_index,
        )
        return False
    return result is True


# ---------------------------------------------------------------------------
# Stateful exit handlers (TRAILING_STOP, TIME_OF_DAY, CHANDELIER)
# delegate to pure-function evaluators in exits.spec.
# ---------------------------------------------------------------------------


def _ctx_to_position(ctx: EvalContext) -> _Position:
    """Build a minimal :class:`positions.model.Position` from the
    strategy-tester's :class:`EvalContext`.

    Only fields read by the spec.py evaluators are populated meaningfully
    (``side``, ``qty_open``, ``avg_entry_price``); the rest get
    placeholders so the dataclass can be constructed. The result is
    NOT meant to leak outside the handler — it's a per-call adapter.
    """
    side: str = "long" if ctx.position_side == "buy" else "short"
    return _Position(
        id=f"strat-test-pos-{ctx.symbol}",
        symbol=ctx.symbol,
        side=side,  # type: ignore[arg-type]
        qty_initial=float(ctx.position_qty),
        qty_open=float(ctx.position_qty),
        avg_entry_price=float(ctx.position_avg_price),
        entry_time=datetime.fromtimestamp(
            int(ctx.position_entry_ts) if ctx.position_entry_ts else 0,
            tz=timezone.utc,
        ),
        source="sandbox",  # type: ignore[arg-type]
    )


def _bar_to_specbar(bar: _BarTuple, bar_ts: int) -> _SpecBar:
    """Wrap the strategy-tester's ``(o,h,l,c)`` tuple as a
    :class:`exits.spec.Bar` with the bar's UTC datetime attached.

    ``bar_ts`` is epoch seconds (matches :attr:`BarSeries.ts`).
    The date is populated tz-aware (UTC) because :func:`from_candles`
    treats naive Candle.date as UTC and downstream handlers only read
    the time component.
    """
    o, h, l, c = bar
    return _SpecBar(
        open=float(o),
        high=float(h),
        low=float(l),
        close=float(c),
        volume=0.0,
        date=datetime.fromtimestamp(int(bar_ts), tz=timezone.utc),
    )


def _exit_trailing_stop(
    trigger: ExitTrigger,
    bar: _BarTuple,
    *,
    ref_price: float,
    position_side: str,
    eval_state: EvalContext | None = None,
    bar_ts: int = 0,
    **_kw: object,
) -> bool:
    """TRAILING_STOP exit: delegate to :func:`exits.spec.update_trail_state`
    + :func:`exits.spec.evaluate_trailing_stop` so headless and live
    semantics stay byte-identical (HWM ratcheting, activation gate,
    trail_basis intrabar/close, percent/dollar units).

    Per-trigger :class:`TriggerState` is stored on
    :attr:`EvalContext.trigger_states`; reset on every position-open
    transition by :func:`_reset_trigger_states_on_activation`.
    """
    if eval_state is None:
        return False
    if trigger.trail_unit is None or trigger.trail_value is None:
        return False
    state = eval_state.trigger_states.get(trigger.id)
    if state is None:
        state = _SpecTriggerState()
        eval_state.trigger_states[trigger.id] = state
    position = _ctx_to_position(eval_state)
    spec_bar = _bar_to_specbar(bar, bar_ts)
    try:
        # Strategy-tester only sees end-of-bar data: pass is_close=True
        # for both trail_basis modes (the spec gates the HWM update on
        # trail_basis internally).
        _spec_update_trail(state, trigger, position, spec_bar, is_close=True)
        decision = _spec_evaluate_trailing(state, trigger, position, spec_bar)
    except Exception:  # noqa: BLE001
        LOG.exception(
            "strategy_tester._exit_trailing_stop: spec evaluator raised "
            "(symbol=%s, trigger_id=%s)", eval_state.symbol, trigger.id,
        )
        return False
    return bool(decision.fire)


def _exit_time_of_day(
    trigger: ExitTrigger,
    bar: _BarTuple,
    *,
    eval_state: EvalContext | None = None,
    bar_ts: int = 0,
    **_kw: object,
) -> bool:
    """TIME_OF_DAY exit: delegate to :func:`exits.spec.evaluate_time_of_day`.

    The ``now`` parameter is built from ``bar_ts`` (epoch seconds) as a
    UTC-aware datetime; the time-of-day comparison uses only the
    HH:MM portion so timezone tagging is informational only. Malformed
    or missing ``time_of_day`` results in silent no-fire (no crash).
    """
    if eval_state is None:
        return False
    if not trigger.time_of_day:
        return False
    position = _ctx_to_position(eval_state)
    spec_bar = _bar_to_specbar(bar, bar_ts)
    # TIME_OF_DAY cutoffs in saved templates are ET HH:MM (matching the
    # ``arm_window_*`` convention). Bar timestamps are UTC epoch
    # seconds, so we convert to ET before handing to
    # :func:`exits.spec.evaluate_time_of_day` — otherwise a "15:55 ET"
    # cutoff on 5m bars whose ts is e.g. ``20:55 UTC`` (= 15:55 ET in
    # winter) would compare ``20:55 >= 15:55`` and fire too early on
    # almost every RTH bar.
    now = _bar_ts_to_et(int(bar_ts))
    try:
        decision = _spec_evaluate_time_of_day(trigger, position, spec_bar, now=now)
    except Exception:  # noqa: BLE001
        LOG.exception(
            "strategy_tester._exit_time_of_day: spec evaluator raised "
            "(symbol=%s, trigger_id=%s)", eval_state.symbol, trigger.id,
        )
        return False
    return bool(decision.fire)


def _exit_chandelier(
    trigger: ExitTrigger,
    bar: _BarTuple,
    *,
    eval_state: EvalContext | None = None,
    bar_ts: int = 0,
    **_kw: object,
) -> bool:
    """CHANDELIER exit: delegate to :func:`exits.spec.update_chandelier_state`
    + :func:`exits.spec.evaluate_chandelier_stop`. Activation-bar handling
    is centralized in :func:`_reset_trigger_states_on_activation`; this
    handler always passes ``is_activation=False`` because by the time we
    reach exit-evaluation the state has already been seeded with
    ``is_activation=True`` for the entry bar.
    """
    if eval_state is None:
        return False
    state = eval_state.trigger_states.get(trigger.id)
    if state is None:
        # Shouldn't happen — the activation reset always creates state
        # for chandelier triggers on the entry bar — but be defensive.
        state = _SpecTriggerState()
        eval_state.trigger_states[trigger.id] = state
    position = _ctx_to_position(eval_state)
    spec_bar = _bar_to_specbar(bar, bar_ts)
    try:
        _spec_update_chandelier(state, trigger, position, spec_bar, is_activation=False)
        decision = _spec_evaluate_chandelier(state, trigger, position, spec_bar)
    except Exception:  # noqa: BLE001
        LOG.exception(
            "strategy_tester._exit_chandelier: spec evaluator raised "
            "(symbol=%s, trigger_id=%s)", eval_state.symbol, trigger.id,
        )
        return False
    return bool(decision.fire)


_EXIT_HANDLERS: dict[ExitTriggerKind, Callable[..., bool]] = {
    ExitTriggerKind.MARKET: _exit_market,
    ExitTriggerKind.LIMIT: _exit_limit,
    ExitTriggerKind.STOP: _exit_stop,
    ExitTriggerKind.STOP_LIMIT: _exit_stop_limit,
    ExitTriggerKind.TRAILING_STOP: _exit_trailing_stop,
    ExitTriggerKind.TIME_OF_DAY: _exit_time_of_day,
    ExitTriggerKind.INDICATOR: _exit_indicator,
    ExitTriggerKind.CHANDELIER: _exit_chandelier,
}


def _reset_trigger_states_on_activation(
    ctx: EvalContext, bar: _BarTuple, bar_ts: int,
) -> None:
    """Seed per-trigger state for stateful exits at position-open.

    Called exactly once per position-open transition (detected via
    :attr:`EvalContext.prev_position_open`). Clears the entire
    ``trigger_states`` dict (a fresh position gets a fresh HWM,
    chandelier window, etc.) then primes the chandelier state with
    ``is_activation=True`` for each enabled CHANDELIER trigger so the
    rolling-high/low window is seeded from the entry bar and the
    ATR running state initialises with the entry bar's close.

    TRAILING_STOP triggers don't need explicit activation —
    :func:`exits.spec.update_trail_state` handles HWM bootstrap from
    the first bar's high/low itself.
    """
    ctx.trigger_states.clear()
    position = _ctx_to_position(ctx)
    spec_bar = _bar_to_specbar(bar, bar_ts)
    for leg in ctx.exit_strategy.legs:
        if not leg.enabled:
            continue
        for trigger in leg.triggers:
            if not trigger.enabled:
                continue
            if trigger.kind is not ExitTriggerKind.CHANDELIER:
                continue
            state = _SpecTriggerState()
            try:
                _spec_update_chandelier(
                    state, trigger, position, spec_bar, is_activation=True,
                )
            except Exception:  # noqa: BLE001
                LOG.exception(
                    "strategy_tester._reset_trigger_states_on_activation: "
                    "chandelier seed failed (symbol=%s, trigger_id=%s)",
                    ctx.symbol, trigger.id,
                )
                continue
            ctx.trigger_states[trigger.id] = state


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
    ctx: EvalContext,
    bar: _BarTuple,
    *,
    eval_ctx: _ScannerEvalContext | None = None,
    normalized_conditions: dict[str, _ScannerGroup] | None = None,
    bar_ts: int = 0,
    et_now: datetime | None = None,
) -> tuple[bool, Side, float]:
    """Decide whether the entry trigger fires against ``bar``.

    Returns ``(fired, side, qty)``. ``qty == 0.0`` means "trigger
    matched but sizing came back zero — treat as not fired".

    ``eval_ctx`` is the per-symbol scanner-engine evaluation context.
    Required for INDICATOR triggers; ignored by price-only handlers.
    ``normalized_conditions`` is a cache of interval-normalized
    condition trees keyed by trigger.id; threaded to INDICATOR
    handlers (price-only handlers ignore it).
    ``bar_ts`` is the UTC epoch-second timestamp of the bar (needed for
    the ``cooldown_secs`` gate). ``et_now`` is the bar timestamp
    converted to ET (needed for ``arm_window`` and
    ``require_market_open`` gates) — supplied by the caller so the
    conversion happens once per bar, not once per handler.
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
    # Arm-window gate (ET HH:MM). Blank → no gate. The default template
    # cooks 09:35–15:30 ET, so without this gate a 24/7 fictional bar
    # series would fire pre-market.
    if et_now is not None and not _within_arm_window(ctx.entry_strategy, et_now):
        return False, Side.BUY, 0.0
    # Require-market-open gate (Mon–Fri AND 09:30 ≤ ET time ≤ 16:00).
    # Holidays are not enforced — synthetic data with a Christmas-day
    # bar will fire, matching the strategy's "any RTH-shaped bar is
    # eligible" interpretation.
    if (
        et_now is not None
        and ctx.entry_strategy.require_market_open
        and not _is_regular_session(et_now)
    ):
        return False, Side.BUY, 0.0
    # Cooldown-since-last-fire gate. ``cooldown_secs == 0`` is the
    # "no cooldown" default. ``last_fire_ts is None`` means "no prior
    # fire" — always passes.
    if (
        ctx.entry_strategy.cooldown_secs > 0
        and ctx.last_fire_ts is not None
        and (bar_ts - ctx.last_fire_ts) < ctx.entry_strategy.cooldown_secs
    ):
        return False, Side.BUY, 0.0

    trigger = ctx.entry_strategy.trigger
    side = (
        Side.BUY
        if ctx.entry_strategy.direction is EntryDirection.LONG
        else Side.SELL
    )

    handler = _ENTRY_HANDLERS.get(trigger.kind, _entry_unsupported)
    fired = handler(
        trigger, bar, side=side, eval_ctx=eval_ctx,
        normalized_conditions=normalized_conditions,
        eval_state=ctx,
    )
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
    ctx: EvalContext,
    bar: _BarTuple,
    *,
    eval_ctx: _ScannerEvalContext | None = None,
    normalized_conditions: dict[str, _ScannerGroup] | None = None,
    bar_ts: int = 0,
) -> tuple[bool, float]:
    """Walk every enabled leg looking for an exit trigger that fires.

    Returns ``(fired, qty_to_close)``. First-leg-to-fire wins (per
    PR-1 simplification — proper OCO is PR 2).

    ``eval_ctx`` is the per-symbol scanner-engine evaluation context.
    Required for INDICATOR triggers; ignored by price-only handlers.
    ``normalized_conditions`` is a cache of interval-normalized
    condition trees keyed by trigger.id; threaded to INDICATOR
    handlers (price-only handlers ignore it).
    ``bar_ts`` is the epoch-seconds timestamp of the current bar;
    threaded to TIME_OF_DAY (needs HH:MM) and TRAILING_STOP /
    CHANDELIER (need a :class:`exits.spec.Bar` with a datetime).
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
                eval_ctx=eval_ctx,
                normalized_conditions=normalized_conditions,
                eval_state=ctx,
                bar_ts=bar_ts,
            )
            if fired:
                pct = max(0.0, min(100.0, float(trigger.qty_pct))) / 100.0
                qty_to_close = ctx.position_qty * pct
                if qty_to_close <= 0.0:
                    continue
                return True, qty_to_close
    return False, 0.0


def _strategy_uses_indicator_trigger(
    entry_strategy: EntryStrategy, exit_strategy: ExitStrategy
) -> bool:
    """Return True if any entry/exit trigger needs the scanner eval_ctx.

    INDICATOR (entry + exit) and SCANNER_ALERT (entry) both delegate to
    :func:`scanner.engine.evaluate_group`, so both require a per-symbol
    :class:`scanner.engine.EvaluationContext`. Skipping context
    construction for pure price-only strategies avoids the
    ``BarsNp.from_candles`` + memo init overhead.
    :class:`EvaluationContext`. Skipping the construction for pure
    price-only strategies avoids the ``BarsNp.from_candles`` + memo
    init overhead.
    """
    if entry_strategy.trigger.kind in (
        EntryTriggerKind.INDICATOR, EntryTriggerKind.SCANNER_ALERT,
    ):
        return True
    for leg in exit_strategy.legs:
        if not leg.enabled:
            continue
        for trig in leg.triggers:
            if not trig.enabled:
                continue
            if trig.kind is ExitTriggerKind.INDICATOR:
                return True
    return False


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

    # Per-symbol scanner-engine context used for INDICATOR triggers.
    # Built lazily so a strategy that uses only MARKET/LIMIT/STOP doesn't
    # pay the cost of bar conversion + indicator memo init.
    # ``current_index`` is mutated bar-by-bar (see _make_or_reset_eval_ctx).
    # Two intervals possible per strategy: entry.trigger.interval and
    # exit-leg.trigger.interval. PR-2 leaves cross-interval / multi-interval
    # indicator triggers to the scanner registry's BarsRegistry path —
    # for now we build a single ctx for the outer ``interval`` and
    # normalize all saved Condition / FieldRef intervals to match the
    # test's outer interval (so a 5m-authored EMA cross can be tested
    # at 1d without the scanner's cross-interval gate silently no-firing).
    eval_ctx: _ScannerEvalContext | None = None
    normalized_conditions: dict[str, _ScannerGroup] = {}
    if _strategy_uses_indicator_trigger(entry_strategy, exit_strategy):
        eval_ctx = _scanner_make_context(
            symbol=symbol,
            interval=interval,
            candles=list(candles),
            current_index=0,
        )
        normalized_conditions = _build_normalized_conditions(
            entry_strategy, exit_strategy, interval,
        )

    n = len(bars)
    for i in range(n):
        if not engine.tick():
            break
        bar = _bar_at(i, bars)
        ts = int(bars.ts[i])
        # Compute the bar's ET datetime ONCE per bar — needed for both
        # the session-day-roll check below AND the arm_window /
        # require_market_open gates in ``_check_entry``.
        et_now = _bar_ts_to_et(ts)
        et_date = et_now.date()

        # Per-ET-trading-day counter reset. Mirrors the live
        # ``EntryEvaluator._roll_session_counters_if_needed`` semantics.
        # ``max_fires_per_session_per_symbol`` means "per trading day",
        # not "per backtest"; without this reset, the default cap of 1
        # caps the entire run at 1 entry per symbol (the smoking-gun
        # "AAPL/NVDA/SPY each have 1 trade" bug).
        if ctx.current_session_et_date != et_date:
            # Per-ET-day ``eod_kill_switch`` flatten. Mirrors the live
            # "market-on-close at 15:55 ET" behaviour: when the ET date
            # rolls and a position is still open from the prior day,
            # synthesise an exit fill at the **prior** bar's close
            # (= EOD of prior trading day). Without this, a 3/8 EMA
            # cross strategy in a trending market would never get a
            # chance to re-enter the next day because the BLOCK policy
            # holds the position open across the date boundary even
            # though the user opted into ``eod_kill_switch``.
            if (
                ctx.current_session_et_date is not None
                and exit_strategy.eod_kill_switch
                and ctx.position_open
                and ctx.position_qty > 0.0
                and i > 0
            ):
                prior_idx = i - 1
                prior_ts = int(bars.ts[prior_idx])
                prior_close = float(bars.close[prior_idx])
                exit_side = Side.SELL if ctx.position_side == "buy" else Side.BUY
                # Use ``apply_fills`` to honour the cost model (slippage
                # + commission) — same shape as the end-of-run kill.
                from ..backtest.fills import apply_fills as _apply_fills
                synth_fills = _apply_fills(
                    orders=[
                        Order(
                            order_id=ctx.mint_order_id(),
                            symbol=symbol,
                            side=exit_side,
                            quantity=float(ctx.position_qty),
                            submitted_ts=prior_ts,
                        )
                    ],
                    next_bar_opens={symbol: prior_close},
                    next_bar_ts=prior_ts,
                    slippage_bps=float(cost_model.slippage_bps),
                    commission=float(cost_model.commission_per_trade),
                    commission_per_share=float(cost_model.commission_per_share),
                )
                for f in synth_fills:
                    engine._apply_fill_with_tracking(f)
                    engine.fills.append(f)
                # Re-sync ctx so the position-state reflects the flatten
                # before the new day's processing begins.
                _sync_position_state_from_engine(ctx, engine, symbol)
            ctx.fires_total = 0
            ctx.fires_by_symbol = 0
            ctx.current_session_et_date = et_date

        if eval_ctx is not None:
            # Decision is made at bar ``i``'s close; reset the per-bar
            # evidence collector so indicator look-back walks don't
            # accumulate evidence across bars.
            eval_ctx.current_index = i
            if eval_ctx.evidence:
                eval_ctx.evidence.clear()

        # Reflect engine-side fills into our context BEFORE checking new triggers.
        # The engine processed any pending order at this tick's open — sync our
        # position-state ledger from the engine portfolio so exit checks see
        # the freshly-opened position on the very same bar (intentional —
        # mirrors the live evaluator's "armed-on-fill" semantics).
        _sync_position_state_from_engine(ctx, engine, symbol)

        # Detect position-open transition (False→True) so stateful exit
        # triggers (TRAILING_STOP, CHANDELIER) can seed their per-trigger
        # :class:`exits.spec.TriggerState` at the entry bar. ``prev_position_open``
        # is updated AFTER the activation reset so an immediate same-bar
        # exit (e.g. take-profit hit on the activation bar) still sees
        # the freshly-seeded chandelier state.
        if ctx.position_open and not ctx.prev_position_open:
            _reset_trigger_states_on_activation(ctx, bar, ts)
        ctx.prev_position_open = ctx.position_open

        # Exit-side first (an open position has priority over re-entry on the same bar).
        if ctx.position_open:
            exit_fired, exit_qty = _check_exits(
                ctx, bar,
                eval_ctx=eval_ctx,
                normalized_conditions=normalized_conditions,
                bar_ts=ts,
            )
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
        fired, side, qty = _check_entry(
            ctx, bar,
            eval_ctx=eval_ctx,
            normalized_conditions=normalized_conditions,
            bar_ts=ts,
            et_now=et_now,
        )
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
            ctx.last_fire_ts = ts

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
