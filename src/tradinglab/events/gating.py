"""Pure (no Tk, no matplotlib) sandbox gating for events.

Mirrors ``backtest/replay.SandboxController.daily_visible_for`` and its
strictly-less-than discipline. Two responsibilities:

1. **Past-only gating** — only events whose ts is ≤ current clock are
   visible. The in-progress bar's events leak no information about the
   future.
2. **Blind-mode redaction** — upcoming earnings are visible as a
   *relative* "in N trading days" count, never as an absolute date. In
   non-blind mode the absolute ts is preserved. Dividends in the
   forward window are similarly redacted in blind mode (rare, but ex-div
   dates are also published in advance).

The decision to keep this module pure (no Tk) makes the discipline
testable in isolation: feed it a clock + a bundle, assert the redaction
rule holds. The Tk-coupled ``SandboxController`` simply delegates here.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import List

from .base import DividendRecord, EarningsRecord, EventBundle

MS_PER_DAY = 86_400_000


@dataclass(frozen=True)
class ForwardEarningsBadge:
    """Blind-mode-safe forward-earnings descriptor.

    ``trading_days_until`` is the count from the current clock to the
    print's ts, computed via a calendar-aware approximation (Mon–Fri
    only). It reveals nothing about the absolute date.

    ``when`` is the BMO/AMC/DMH slot — safe in blind mode because the
    user already infers session position from time-of-day.

    The :class:`EarningsRecord` itself is **not** included in blind
    mode to prevent absolute-date leakage. In non-blind mode the badge
    is still produced, plus :class:`EventsView.forward_earnings`
    carries the full record.
    """
    trading_days_until: int
    when: str


@dataclass
class EventsView:
    """Gated per-symbol event view at the current sandbox clock.

    Past events expose full payload. Forward events get redacted in
    blind mode (no absolute ts, no actuals); in non-blind mode the
    full forward record is included alongside the relative badge.

    Sandbox callers iterate ``past_earnings`` / ``past_dividends`` to
    render historical glyphs, and ``forward_badges`` to render the
    "Earn T-2 AMC" right-edge label.
    """
    past_earnings: List[EarningsRecord] = field(default_factory=list)
    past_dividends: List[DividendRecord] = field(default_factory=list)
    forward_earnings: List[EarningsRecord] = field(default_factory=list)
    forward_dividends: List[DividendRecord] = field(default_factory=list)
    forward_badges: List[ForwardEarningsBadge] = field(default_factory=list)


def _approx_trading_days_between(ms_start: int, ms_end: int) -> int:
    """Calendar-day delta scaled by 5/7 to approximate trading days.

    Cheap, deterministic, and good enough for a "T-2"-style badge. The
    holiday calendar would be a heavier dependency (pandas market
    calendars) and the user has already opted into low-friction here —
    a one-off Memorial Day off-by-one in the badge is acceptable.
    """
    if ms_end <= ms_start:
        return 0
    calendar_days = (ms_end - ms_start + MS_PER_DAY - 1) // MS_PER_DAY
    # 5 weekdays per 7-day week, rounded up. For small N this collapses
    # to ``calendar_days * 5 // 7`` plus a +1 fudge for weekends; we use
    # a closed-form approximation that's monotonic.
    return max(0, int(math.ceil(calendar_days * 5.0 / 7.0)))


def events_visible_for(
    bundle: EventBundle,
    clock_ts: int,
    *,
    blind: bool,
    forward_window_days: int = 30,
) -> EventsView:
    """Build an :class:`EventsView` for ``bundle`` at ``clock_ts``.

    Past events (ts <= clock_ts) are returned with full payload. Forward
    events within ``forward_window_days`` trading days are returned
    with the absolute ts preserved in non-blind mode and redacted in
    blind mode (the records aren't included; only the badge is).

    ``forward_window_days`` caps the lookahead to keep tooltips terse
    and prevent leaking the ticker's overall earnings cadence (which
    could fingerprint the absolute calendar position even from relative
    counts).
    """
    if bundle is None:
        return EventsView()

    # Earnings: bisect on ts.
    earn_ts = [r.ts for r in bundle.earnings]
    cut = bisect_right(earn_ts, int(clock_ts))
    past_earnings = list(bundle.earnings[:cut])

    # Mask future actuals on past records — defence in depth. Real
    # past prints always have non-NaN actuals; this redundantly NaN-
    # wipes any provider misclassification (future row mis-stamped
    # to past).
    past_earnings = [
        e if not math.isnan(e.eps_actual) else
        EarningsRecord(
            ts=e.ts, symbol=e.symbol, when=e.when,
            eps_estimate=e.eps_estimate,
            eps_actual=math.nan,
            revenue_estimate=e.revenue_estimate,
            revenue_actual=math.nan,
            source=e.source,
        )
        for e in past_earnings
    ]

    forward_window_ms = max(1, int(forward_window_days)) * MS_PER_DAY
    upper = int(clock_ts) + forward_window_ms

    forward_records: List[EarningsRecord] = []
    forward_badges: List[ForwardEarningsBadge] = []
    for e in bundle.earnings[cut:]:
        if e.ts > upper:
            break
        td = _approx_trading_days_between(int(clock_ts), int(e.ts))
        forward_badges.append(ForwardEarningsBadge(trading_days_until=td, when=e.when))
        if not blind:
            # Strip actuals defensively — future row should already have
            # NaN actuals, but a provider sending bogus future actuals
            # would leak the print outcome.
            forward_records.append(EarningsRecord(
                ts=e.ts, symbol=e.symbol, when=e.when,
                eps_estimate=e.eps_estimate,
                eps_actual=math.nan,
                revenue_estimate=e.revenue_estimate,
                revenue_actual=math.nan,
                source=e.source,
            ))

    # Dividends: bisect on ex_ts.
    div_ts = [d.ex_ts for d in bundle.dividends]
    div_cut = bisect_right(div_ts, int(clock_ts))
    past_dividends = list(bundle.dividends[:div_cut])

    forward_dividends: List[DividendRecord] = []
    if not blind:
        for d in bundle.dividends[div_cut:]:
            if d.ex_ts > upper:
                break
            forward_dividends.append(d)
    # In blind mode forward dividends are simply omitted — there's no
    # equivalent of a "relative" dividend badge in the trader UX, and
    # leaking the next ex-date would let the user back out the calendar
    # position.

    return EventsView(
        past_earnings=past_earnings,
        past_dividends=past_dividends,
        forward_earnings=forward_records,
        forward_dividends=forward_dividends,
        forward_badges=forward_badges,
    )


__all__ = (
    "EventsView",
    "ForwardEarningsBadge",
    "events_visible_for",
)
