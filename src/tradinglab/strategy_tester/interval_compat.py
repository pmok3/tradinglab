"""Interval-compatibility checks for entry/exit strategies.

Some indicators are **intraday-only** — VWAP, the cumulative /
time-of-day modes of RVOL / RRVOL, and Prior Day High/Low. A strategy
that references one of them on a daily / weekly / monthly interval
silently produces **no signals**: the indicator resolves to NaN on
every bar, so any condition that reads it (e.g. ``close > vwap``)
evaluates to *unknown* under the engine's tri-valued logic and never
fires.

This module detects that mismatch BEFORE the strategy runs so the GUI
can show the user a clear popup and block the action, instead of letting
them stare at a 0-signal result and wonder why. It serves three call
sites:

* **Strategy Tester** (`incompatible_indicators_for_interval`) — the run
  normalizes every condition to the single ``cfg.interval``, so every
  referenced indicator is checked against that one interval.
* **Live arming** (`incompatible_arming_problems` with
  ``available_intervals=None``) — the live evaluator respects each
  condition's own interval, so each indicator is checked against its
  resolved interval. Only an intraday-only indicator pinned to a
  non-intraday interval is a problem (a 5m VWAP strategy stays armable —
  it works live on 5m bars regardless of the chart interval).
* **Sandbox arming** (`incompatible_arming_problems` with a non-None
  ``available_intervals``) — the sandbox can only serve the intervals it
  was configured with (its primary tick interval + integer multiples).
  A strategy whose condition tree needs a finer interval than the
  sandbox provides (e.g. a 5m strategy in a 1d-only sandbox) can never
  fire, so it is blocked too.

The single source of truth for "is this indicator available on this
interval" is :func:`tradinglab.indicators.base.factory_is_available_for`
(which consults each factory's ``is_available_for`` method). The set of
referenced indicators for the normalized (tester) check is collected by
:func:`tradinglab.strategy_tester.warmup.collect_referenced_indicator_kinds`,
shared with the warmup sizer so both walk the exact same surface.
"""
from __future__ import annotations

from ..entries.model import EntryStrategy
from ..exits.model import ExitStrategy
from ..indicators.base import factory_by_kind_id, factory_is_available_for
from ..scanner.model import Condition, FieldRef, Group
from .warmup import collect_referenced_indicator_kinds

__all__ = [
    "incompatible_indicators_for_interval",
    "incompatible_arming_problems",
]

#: Friendly interval labels for the user-facing popup.
_INTERVAL_LABELS = {
    "1m": "1-minute", "2m": "2-minute", "5m": "5-minute",
    "15m": "15-minute", "30m": "30-minute", "1h": "1-hour",
    "1d": "daily", "1w": "weekly", "1wk": "weekly", "1mo": "monthly",
}


def _pretty_interval(interval: str) -> str:
    label = _INTERVAL_LABELS.get(interval)
    return f"{label} ({interval})" if label else interval




def incompatible_indicators_for_interval(
    entry_strategy: EntryStrategy | None,
    exit_strategy: ExitStrategy | None,
    interval: str,
) -> list[tuple[str, str]]:
    """Return ``[(display_name, reason), ...]`` for every referenced
    indicator that is **not** available on ``interval``.

    An empty list means every indicator the strategies reference works
    on ``interval`` (so the Run is safe to proceed). The result is
    de-duplicated by display name (first reason wins) and order-stable,
    so the caller can render it directly in a popup.

    ``interval`` is the Strategy Tester run interval (e.g. ``"1d"`` /
    ``"5m"``). A blank interval returns ``[]`` (nothing to validate).
    Unknown ``kind_id``s and indicators without an ``is_available_for``
    declaration are treated as available (fail-open) — only an explicit
    "not available on this interval" blocks the Run.
    """
    if not interval:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _symbol, kind_id, params in collect_referenced_indicator_kinds(
        entry_strategy, exit_strategy
    ):
        resolved = factory_by_kind_id(kind_id)
        if resolved is None:
            continue
        display_name, factory = resolved
        avail = factory_is_available_for(factory, interval, params)
        if avail.ok:
            continue
        if display_name in seen:
            continue
        seen.add(display_name)
        out.append(
            (display_name, avail.reason or "Requires an intraday interval")
        )
    return out


def _walk_condition_tree(
    node: Group | Condition | None,
    parent_interval: str,
    *,
    intervals: set[str],
    indicator_refs: list[tuple[str, dict, str]],
) -> None:
    """Collect every interval the condition tree reads bars at, plus each
    indicator FieldRef paired with its resolved interval.

    ``intervals`` accumulates ``condition.interval`` (or the inherited
    ``parent_interval`` when blank) for every :class:`Condition`, plus any
    per-:class:`FieldRef` interval override. ``indicator_refs`` accumulates
    ``(kind_id, params, resolved_interval)`` for every ``kind="indicator"``
    field, where the resolved interval is ``field.interval`` if overridden
    else the condition's interval.
    """
    if node is None:
        return
    if isinstance(node, Condition):
        cond_interval = node.interval or parent_interval
        if cond_interval:
            intervals.add(cond_interval)
        for ref in (node.left, *(node.params or {}).values()):
            if not isinstance(ref, FieldRef):
                continue
            if ref.interval:
                intervals.add(ref.interval)
            if ref.kind == "indicator" and ref.id:
                resolved = ref.interval or cond_interval
                indicator_refs.append(
                    (str(ref.id), dict(ref.params or {}), resolved)
                )
        return
    if isinstance(node, Group):
        for child in node.children:
            _walk_condition_tree(
                child, parent_interval,
                intervals=intervals, indicator_refs=indicator_refs,
            )


def incompatible_arming_problems(
    entry_strategy: EntryStrategy,
    *,
    available_intervals: frozenset[str] | None = None,
    fallback_interval: str = "1m",
) -> list[str]:
    """Return human-readable problems that would stop ``entry_strategy``
    from ever firing in the current context.

    Unlike :func:`incompatible_indicators_for_interval` (which normalizes
    every condition to one run interval), this respects each condition's
    **own** interval — matching the live + sandbox evaluators, which read
    bars for ``(symbol, trigger.interval or default_interval)`` and honor
    per-field interval overrides.

    Parameters
    ----------
    available_intervals
        ``None`` (live): every interval is fetchable on demand, so the
        only problem is an intraday-only indicator pinned to a
        non-intraday interval. A non-empty frozenset (sandbox): the only
        intervals the context can serve — a condition tree that needs an
        interval outside this set can never get bars and is flagged.
    fallback_interval
        The interval a condition with a blank ``interval`` resolves to —
        the live/sandbox evaluator's ``default_interval`` (``"1m"``).

    Only triggers that carry a condition tree (INDICATOR triggers) are
    inspected; a MARKET trigger has no condition tree, reads no indicator
    bars, and is never flagged (it fires on the tick regardless of
    interval). SCANNER_ALERT triggers reference an on-disk scan that is
    not walked here. The result is order-stable and de-duplicated.
    """
    trigger = getattr(entry_strategy, "trigger", None)
    condition = getattr(trigger, "condition", None) if trigger else None
    if condition is None:
        return []
    parent_interval = (getattr(trigger, "interval", "") or fallback_interval)
    intervals: set[str] = set()
    indicator_refs: list[tuple[str, dict, str]] = []
    _walk_condition_tree(
        condition, parent_interval,
        intervals=intervals, indicator_refs=indicator_refs,
    )

    problems: list[str] = []
    seen: set[str] = set()

    # 1) Data availability (sandbox only): a required interval the
    #    context can't serve. Live (available_intervals is None) skips
    #    this — any interval is fetchable on demand.
    if available_intervals is not None:
        for itv in sorted(intervals):
            if itv and itv not in available_intervals:
                key = f"interval:{itv}"
                if key in seen:
                    continue
                seen.add(key)
                problems.append(
                    f"needs {_pretty_interval(itv)} bars, which this "
                    f"sandbox session doesn't provide"
                )

    # 2) Indicator availability (live + sandbox): an intraday-only
    #    indicator at a non-intraday resolved interval. Skip refs whose
    #    interval was already flagged as unavailable above (the interval
    #    problem subsumes them — no need to double-report).
    for kind_id, params, itv in indicator_refs:
        if available_intervals is not None and itv not in available_intervals:
            continue
        resolved = factory_by_kind_id(kind_id)
        if resolved is None:
            continue
        display_name, factory = resolved
        avail = factory_is_available_for(factory, itv, params)
        if avail.ok:
            continue
        key = f"indicator:{display_name}"
        if key in seen:
            continue
        seen.add(key)
        problems.append(
            f"{display_name} \u2014 "
            f"{avail.reason or 'requires an intraday interval'}"
        )

    return problems
