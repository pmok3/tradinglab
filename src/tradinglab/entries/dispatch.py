"""Shared trigger-dispatch registry for entry strategies.

Audit item #4 (CLAUDE.md §7.20). Both the live ``EntryEvaluator``
(:mod:`tradinglab.entries.evaluator`) and the mechanical
``strategy_tester`` evaluator (:mod:`tradinglab.strategy_tester.evaluator`)
delegate the "does this trigger fire on this bar?" decision to the
:data:`_ENTRY_DISPATCH` registry below. The two evaluators each keep
their own context-building logic (live = ``EvaluationContext`` from a
scanner row + ``BarsRegistry`` view; mechanical = per-symbol
``_ScannerEvalContext`` with EOD kill / RTH-only filter / etc.) but
the actual fire decision is centralized so adding a new
:class:`TriggerKind` lights up both call sites at once and drift is
structurally impossible.

Returns a uniform ``(fires: bool, evidence: list[MatchEvidence])`` tuple
for every handler. Price-only handlers return an empty evidence list;
INDICATOR / SCANNER_ALERT handlers surface
``MatchEvidence`` from the underlying scanner-engine evaluation so
audit logs and replay overlays can render "EMA cross fired 1 bar ago
at 10:35" lines.

Both call sites are expected to populate :class:`TriggerContext` with
only the fields their trigger kind requires; unset fields default to
``None`` and handlers degrade gracefully (silent no-fire) instead of
raising.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from ..scanner.engine import evaluate_group as _evaluate_group
from ..scanner.model import MatchEvidence
from .model import Direction, EntryTrigger, TriggerKind
from .signals import EntryOrderKind
from .spec import (
    should_fire_limit,
    should_fire_market,
    should_fire_stop,
    should_fire_stop_limit,
)

LOG = logging.getLogger(__name__)

__all__ = [
    "BarView",
    "TriggerContext",
    "TriggerHandler",
    "check_trigger_fires",
    "reference_price",
    "signal_price_for_kind",
    "supported_trigger_kinds",
    "_ENTRY_DISPATCH",
]


@dataclass(frozen=True)
class BarView:
    """Tiny adapter exposing ``open / high / low / close`` floats.

    The live evaluator passes :class:`tradinglab.exits.spec.Bar` /
    :class:`tradinglab.models.Candle` objects; the mechanical
    evaluator passes a ``(open, high, low, close)`` 4-tuple. Both
    shapes are accepted by :meth:`from_any`.
    """

    open: float
    high: float
    low: float
    close: float

    @classmethod
    def from_any(cls, bar: Any) -> BarView:
        if isinstance(bar, tuple) and len(bar) == 4:
            o, h, lo, c = bar
            return cls(float(o), float(h), float(lo), float(c))
        return cls(
            open=float(getattr(bar, "open", 0.0) or 0.0),
            high=float(getattr(bar, "high", 0.0) or 0.0),
            low=float(getattr(bar, "low", 0.0) or 0.0),
            close=float(getattr(bar, "close", 0.0) or 0.0),
        )


@dataclass
class TriggerContext:
    """Bundle of every context arg any handler might need.

    Each call site populates the fields its trigger kind requires:

    * Price-only handlers (MARKET / LIMIT / STOP / STOP_LIMIT) read
      :attr:`direction`, :attr:`bar`, and :attr:`is_close` only.
    * INDICATOR handler reads :attr:`scanner_eval_ctx` (required) +
      :attr:`normalized_conditions` (optional cache).
    * SCANNER_ALERT handler reads :attr:`scanner_row` (live path —
      ScanRunner already filtered new_rows so a non-None row means
      "fired") OR :attr:`scanner_eval_ctx` +
      :attr:`scanner_alert_prev_match` (mechanical path — per-bar
      edge detection against the stored prev-match state).

    Unset optional fields default to ``None`` and the handlers
    degrade to a silent no-fire instead of raising. This matches the
    defensive contract both evaluators expected before unification.
    """

    direction: Direction
    bar: BarView
    is_close: bool = True
    # INDICATOR / SCANNER_ALERT scanner-engine context (live path
    # builds it from a BarsRegistry view; mechanical path builds it
    # once per symbol outside the bar loop).
    scanner_eval_ctx: Any | None = None
    # Optional cache of interval-normalized condition trees keyed by
    # ``trigger.id`` — built once per symbol by the mechanical
    # evaluator. The live evaluator does not normalize (its bars come
    # from the registry already at the requested interval) and leaves
    # this ``None``.
    normalized_conditions: dict[str, Any] | None = None
    # SCANNER_ALERT live path — the pre-filtered new_row from
    # ScanRunner. When non-None the live handler short-circuits to
    # "fired" because ScanRunner already did the edge detection.
    scanner_row: Any = None
    # SCANNER_ALERT mechanical edge-trigger state, keyed by
    # ``trigger.id``. Bar-0 sets the entry to the current match value
    # and returns no-fire; subsequent False→True transitions fire.
    scanner_alert_prev_match: dict[str, bool] | None = None


class TriggerHandler(Protocol):
    """Signature every entry in :data:`_ENTRY_DISPATCH` must satisfy."""

    def __call__(
        self, trigger: EntryTrigger, ctx: TriggerContext,
    ) -> tuple[bool, list[MatchEvidence]]: ...


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _h_market(
    trigger: EntryTrigger, ctx: TriggerContext,
) -> tuple[bool, list[MatchEvidence]]:
    return should_fire_market(trigger, ctx.bar, is_close=ctx.is_close), []


def _h_limit(
    trigger: EntryTrigger, ctx: TriggerContext,
) -> tuple[bool, list[MatchEvidence]]:
    return should_fire_limit(trigger, ctx.bar, direction=ctx.direction), []


def _h_stop(
    trigger: EntryTrigger, ctx: TriggerContext,
) -> tuple[bool, list[MatchEvidence]]:
    return should_fire_stop(trigger, ctx.bar, direction=ctx.direction), []


def _h_stop_limit(
    trigger: EntryTrigger, ctx: TriggerContext,
) -> tuple[bool, list[MatchEvidence]]:
    return should_fire_stop_limit(trigger, ctx.bar, direction=ctx.direction), []


def _h_indicator(
    trigger: EntryTrigger, ctx: TriggerContext,
) -> tuple[bool, list[MatchEvidence]]:
    """INDICATOR trigger: evaluate ``trigger.condition`` via the scanner kernel.

    Requires :attr:`TriggerContext.scanner_eval_ctx` (the live evaluator
    builds it from ``BarsRegistry.get_view``; the mechanical evaluator
    builds it once per symbol outside the bar loop). When the
    optional :attr:`normalized_conditions` cache is supplied (mechanical
    path), the rewritten interval-forced tree is used instead of
    ``trigger.condition`` — this is the only difference between live
    and mechanical paths and it never changes the boolean outcome
    when the live registry already provides matching-interval bars.

    Silently no-fire on missing condition / missing context /
    ``is_close=False`` / ``NotImplementedError`` (cross-interval
    indicator path the scanner doesn't yet support) / generic
    exception (logged via :data:`LOG`). The defensive contract is
    that a broken indicator never raises out of the dispatch — both
    evaluators count on this to keep the bar loop alive.
    """
    if not ctx.is_close:
        return False, []
    if ctx.scanner_eval_ctx is None or trigger.condition is None:
        return False, []
    condition = trigger.condition
    if ctx.normalized_conditions is not None:
        condition = ctx.normalized_conditions.get(trigger.id, condition)
    try:
        result = _evaluate_group(condition, ctx.scanner_eval_ctx)
        evidence = list(getattr(ctx.scanner_eval_ctx, "evidence", []) or [])
    except NotImplementedError:
        return False, []
    except Exception:  # noqa: BLE001 — defensive; never crash the bar loop
        LOG.exception(
            "entries.dispatch._h_indicator: evaluate_group raised "
            "(trigger_id=%s)", getattr(trigger, "id", "?"),
        )
        return False, []
    return (result is True), evidence


def _h_scanner_alert(
    trigger: EntryTrigger, ctx: TriggerContext,
) -> tuple[bool, list[MatchEvidence]]:
    """SCANNER_ALERT trigger: two paths, one registry slot.

    **Live path** (``ctx.scanner_row`` non-None): the
    :class:`~tradinglab.scanner.runner.ScanRunner` subscription already
    did the new-row / edge-detection work. The presence of a row IS
    the fire signal. Evidence is the row's pre-computed evidence list.

    **Mechanical path** (``ctx.scanner_eval_ctx`` non-None +
    ``ctx.scanner_alert_prev_match`` non-None): per-bar evaluation
    with explicit edge-trigger semantics. Bar-0 records the current
    match into ``scanner_alert_prev_match[trigger.id]`` without firing;
    subsequent False/None → True transitions fire. This avoids the
    backtest gotcha where every already-matching symbol fires on the
    first bar.

    Silently no-fire on any of: missing ``scanner_id``, missing
    ``normalized_conditions``, condition not in the cache (scan failed
    to load at evaluator startup), ``NotImplementedError``, or generic
    exception (logged). The live and mechanical paths intentionally
    surface different evidence shapes — see callers for how each
    consumes it.
    """
    if ctx.scanner_row is not None:
        row_evidence = list(getattr(ctx.scanner_row, "evidence", []) or [])
        return True, row_evidence
    if ctx.scanner_eval_ctx is None or not trigger.scanner_id:
        return False, []
    if ctx.normalized_conditions is None:
        return False, []
    condition = ctx.normalized_conditions.get(trigger.id)
    if condition is None:
        return False, []
    try:
        result = _evaluate_group(condition, ctx.scanner_eval_ctx)
    except NotImplementedError:
        return False, []
    except Exception:  # noqa: BLE001
        LOG.exception(
            "entries.dispatch._h_scanner_alert: evaluate_group raised "
            "(trigger_id=%s, scanner_id=%s)",
            getattr(trigger, "id", "?"), trigger.scanner_id,
        )
        return False, []
    matched_now = result is True
    prev = None
    if ctx.scanner_alert_prev_match is not None:
        prev = ctx.scanner_alert_prev_match.get(trigger.id)
        ctx.scanner_alert_prev_match[trigger.id] = matched_now
    if prev is None:
        return False, []
    return (matched_now and not prev), []


# ---------------------------------------------------------------------------
# Registry — the single source of truth shared by both evaluators
# ---------------------------------------------------------------------------


_ENTRY_DISPATCH: dict[TriggerKind, TriggerHandler] = {
    TriggerKind.MARKET: _h_market,
    TriggerKind.LIMIT: _h_limit,
    TriggerKind.STOP: _h_stop,
    TriggerKind.STOP_LIMIT: _h_stop_limit,
    TriggerKind.INDICATOR: _h_indicator,
    TriggerKind.SCANNER_ALERT: _h_scanner_alert,
}


def supported_trigger_kinds() -> set[TriggerKind]:
    """Return the set of trigger kinds currently registered.

    Stable contract for GUI "Supported" lists and tests. Mutating the
    underlying :data:`_ENTRY_DISPATCH` dict immediately reflects here.
    """
    return set(_ENTRY_DISPATCH.keys())


# ---------------------------------------------------------------------------
# Facades
# ---------------------------------------------------------------------------


def check_trigger_fires(
    trigger: EntryTrigger,
    ctx: TriggerContext,
) -> tuple[bool, list[MatchEvidence]]:
    """Look up :attr:`trigger.kind` in :data:`_ENTRY_DISPATCH` and dispatch.

    Returns ``(False, [])`` when no handler is registered — callers
    that need a typed "unsupported" signal (e.g. the mechanical
    strategy_tester's ``UnsupportedTriggerKind``) should check
    :func:`supported_trigger_kinds` themselves before calling.
    """
    handler = _ENTRY_DISPATCH.get(trigger.kind)
    if handler is None:
        return False, []
    return handler(trigger, ctx)


def reference_price(trigger: EntryTrigger, bar: Any) -> float | None:
    """Pick the price used for sizing + risk-gate evaluation.

    Mirrors the live evaluator's prior ``_reference_price`` helper so
    the post-fill review screen and the live fire path agree. Maps:

    * ``MARKET`` / ``INDICATOR`` / ``SCANNER_ALERT`` → ``bar.close``
    * ``LIMIT`` / ``STOP_LIMIT`` → ``trigger.price`` (limit ceiling)
    * ``STOP`` → ``trigger.stop_price``

    Returns ``None`` when the required field is missing or coerces to
    a non-finite float (caller blocks the fire with a
    ``no_reference_price`` audit reason).
    """
    kind = trigger.kind
    try:
        if kind == TriggerKind.LIMIT:
            return float(trigger.price) if trigger.price else None
        if kind == TriggerKind.STOP_LIMIT:
            return float(trigger.price) if trigger.price else None
        if kind == TriggerKind.STOP:
            return float(trigger.stop_price) if trigger.stop_price else None
        return float(getattr(bar, "close", 0.0))
    except (TypeError, ValueError):
        return None


def signal_price_for_kind(
    kind: TriggerKind, trigger: EntryTrigger, bar: Any,
) -> tuple[EntryOrderKind, float | None, float | None]:
    """Return ``(order_kind, signal.price, signal.limit_price)``.

    MARKET / INDICATOR / SCANNER_ALERT collapse to a MARKET paper
    order with no working price (the paper engine fills at
    ``bar.close``). LIMIT / STOP carry their working price; STOP_LIMIT
    carries both stop and limit.

    Behaviour-preserving port of the live evaluator's prior
    ``_signal_price_for_kind`` helper. ``bar`` is unused by the
    current cases but accepted so future kinds (e.g. trailing-entry)
    can derive working prices from bar shape without changing the
    facade signature.
    """
    _ = bar  # reserved for future kinds
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
    return (EntryOrderKind.MARKET, None, None)
