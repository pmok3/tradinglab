"""Render-time helpers for the events feature.

Pure functions that map an :class:`~tradinglab.events.gating.EventsView`
plus a visible-candle window into a list of glyph *descriptors* —
``(bar_index, glyph_kind, tooltip)`` triples — that the GUI overlay
layer turns into matplotlib artists.

Keeping the descriptor build pure (no matplotlib dependency) means:

* the smoke tests can exercise the glyph-positioning logic without an
  X server.
* the same descriptors drive Compare panes, the primary chart, and any
  future Performance-View thumbnail without duplicating placement
  math.

Glyph kinds preserve event semantics for filtering and hover, while
``EventGlyph.marker_glyph`` carries the on-chart text marker:

* Earnings AMC → ``"A"``
* Earnings BMO → ``"B"``
* Dividend ex-date → ``"D"``
* Splits keep ``"S"``; unsupported earnings slots fall back to ``"E"``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Glyph kind canonical strings (importers may match on these).
GLYPH_EARNINGS_PAST = "E"
GLYPH_EARNINGS_FORWARD = "E?"
GLYPH_DIVIDEND = "D"
GLYPH_SPECIAL_DIVIDEND = "D*"
GLYPH_SPLIT = "S"

# On-chart text labels requested by the user-facing event taxonomy.
EVENT_MARKER_GLYPH = {
    "earnings_amc": "A",
    "earnings_bmo": "B",
    "dividend": "D",
}


@dataclass(frozen=True)
class EventGlyph:
    """Per-event placement descriptor.

    ``bar_index`` is the integer index into the *visible* candle list
    of the bar this glyph anchors on. ``-1`` indicates the event sits
    outside the visible window and should be rendered as a "right-
    edge forward badge" — the renderer paints these flush against
    the right spine with a relative tooltip ("Earn T-2 AMC").

    ``glyph_kind`` is one of the ``GLYPH_*`` constants above.

    ``tooltip`` is the pre-built hover string. The renderer pipes it
    straight to the hover annotation without further formatting so
    that blind-mode redaction stays inside this module.

    ``ts_ms`` is the event's UTC ms-since-epoch ts — useful for the
    hover hit-test (matching the event under the cursor) but not
    used for placement (``bar_index`` already encodes that).

    ``marker_glyph`` is the literal text painted in-pane. It stays
    separate from ``glyph_kind`` so renderers can filter semantic event
    kinds while drawing user-facing A/B/D labels.
    """
    bar_index: int
    glyph_kind: str
    tooltip: str
    ts_ms: int
    marker_glyph: str = ""


MS_PER_DAY = 86_400_000


def _bar_index_for_ts(
    candles: Sequence[Any],
    ts_ms: int,
) -> int:
    """Return the index of the candle whose calendar day contains
    ``ts_ms``, or ``-1`` if none.

    Walks the candle list in order — visible windows are O(100s) of
    bars, so a linear pass is faster than the bisect setup cost.
    Compares on the UTC date of each candle's ``date`` attribute
    against the UTC date of ``ts_ms`` (events are day-resolution).
    """
    target_day = ts_ms // MS_PER_DAY
    for i, c in enumerate(candles):
        d = getattr(c, "date", None)
        if d is None:
            continue
        try:
            ts_s = int(d.timestamp())
        except (TypeError, ValueError, OverflowError):
            continue
        cand_day = (ts_s * 1000) // MS_PER_DAY
        if cand_day == target_day:
            return i
    return -1


def _build_day_index_map(candles: Sequence[Any]) -> dict[int, int]:
    """Map each UTC-day to the FIRST candle index on that day.

    One O(N) pass (a single ``date.timestamp()`` per candle) replaces the
    per-event O(N) re-scan in :func:`_bar_index_for_ts`, so glyph projection
    in :func:`build_event_glyphs` is O(bars + events) instead of
    O(events × bars) — the dominant per-render events cost when a symbol has
    many dividends / earnings prints in view. First-index-wins matches the
    linear scan's "return the first candle whose calendar day matches"
    contract.
    """
    day_map: dict[int, int] = {}
    for i, c in enumerate(candles):
        d = getattr(c, "date", None)
        if d is None:
            continue
        try:
            ts_s = int(d.timestamp())
        except (TypeError, ValueError, OverflowError):
            continue
        day = (ts_s * 1000) // MS_PER_DAY
        if day not in day_map:
            day_map[day] = i
    return day_map


def _format_eps(value: float) -> str:
    if math.isnan(value):
        return "—"
    return f"{value:+.2f}"


def _format_revenue(value: float) -> str:
    if math.isnan(value):
        return "—"
    abs_val = abs(value)
    if abs_val >= 1e9:
        return f"${value / 1e9:.2f}B"
    if abs_val >= 1e6:
        return f"${value / 1e6:.2f}M"
    if abs_val >= 1e3:
        return f"${value / 1e3:.2f}K"
    return f"${value:.0f}"


def _earnings_tooltip(record: Any, *, future: bool) -> str:
    """Build the per-earnings hover string.

    Past prints include EPS estimate / actual / surprise %. Forward
    prints include only the estimate plus the BMO/AMC slot —
    ``EarningsRecord.eps_actual`` is NaN on forward rows so there is
    nothing to leak.
    """
    when = str(getattr(record, "when", "") or "").strip()
    lines: list[str] = []
    if future:
        lines.append(f"Earnings (upcoming) {when}".strip())
        est = float(getattr(record, "eps_estimate", math.nan))
        lines.append(f"  EPS est: {_format_eps(est)}")
    else:
        lines.append(f"Earnings {when}".strip())
        est = float(getattr(record, "eps_estimate", math.nan))
        act = float(getattr(record, "eps_actual", math.nan))
        lines.append(f"  EPS est: {_format_eps(est)}  act: {_format_eps(act)}")
        if not math.isnan(est) and not math.isnan(act) and est != 0.0:
            surprise = (act - est) / abs(est) * 100.0
            lines.append(f"  Surprise: {surprise:+.1f}%")
        rev_est = float(getattr(record, "revenue_estimate", math.nan))
        rev_act = float(getattr(record, "revenue_actual", math.nan))
        if not (math.isnan(rev_est) and math.isnan(rev_act)):
            lines.append(
                f"  Rev est: {_format_revenue(rev_est)}  "
                f"act: {_format_revenue(rev_act)}"
            )
    return "\n".join(lines)


#: Per-dividend-kind glyph for the price-pane in-bar marker. Keys
#: are normalised ``record.kind`` strings; missing kinds (or the
#: default ``"cash"``) fall back to ``GLYPH_DIVIDEND``. Splits get
#: their own ``GLYPH_SPLIT`` so a 4:1 split doesn't look like a
#: cash distribution. Single source of truth for the three call
#: sites (``_dividend_marker_glyph``, ``_dividend_tooltip``,
#: ``build_event_glyphs``).
_DIVIDEND_GLYPH_BY_KIND: dict[str, str] = {
    "stock_split": GLYPH_SPLIT,
    "special":     GLYPH_SPECIAL_DIVIDEND,
    "spinoff":     GLYPH_SPECIAL_DIVIDEND,
}


def _dividend_glyph_for_kind(kind: str) -> str:
    """Map ``record.kind`` to a ``GLYPH_*`` constant.

    Defaults to ``GLYPH_DIVIDEND`` for the plain ``"cash"`` case and
    any unknown kind. Used by :func:`build_event_glyphs` for the
    price-pane glyph; the marker (in-bar label) is handled by
    :func:`_dividend_marker_glyph`.
    """
    return _DIVIDEND_GLYPH_BY_KIND.get(kind, GLYPH_DIVIDEND)


def _dividend_tooltip(record: Any) -> str:
    kind = str(getattr(record, "kind", "cash") or "cash")
    if kind == "stock_split":
        num = int(getattr(record, "ratio_num", 1) or 1)
        den = int(getattr(record, "ratio_den", 1) or 1)
        return f"Stock split {num}:{den}"
    amount = float(getattr(record, "amount", 0.0) or 0.0)
    tooltip_prefix = _DIVIDEND_TOOLTIP_PREFIX_BY_KIND.get(kind, "Dividend")
    return f"{tooltip_prefix} ${amount:.4f}/sh"


#: Per-dividend-kind tooltip phrase (everything before the
#: ``" $X.XXXX/sh"`` suffix). Same registry pattern as
#: ``_DIVIDEND_GLYPH_BY_KIND`` but for the hover-tooltip layer.
#: Note: ``"stock_split"`` is intentionally absent — splits use
#: a totally different tooltip format (ratio not amount) and are
#: handled with an early return above.
_DIVIDEND_TOOLTIP_PREFIX_BY_KIND: dict[str, str] = {
    "special": "Special dividend",
    "spinoff": "Spinoff (cash credit)",
}


def _earnings_marker_glyph(record: Any) -> str:
    when = str(getattr(record, "when", "") or "").strip().upper()
    if when == "AMC":
        return EVENT_MARKER_GLYPH["earnings_amc"]
    if when == "BMO":
        return EVENT_MARKER_GLYPH["earnings_bmo"]
    return GLYPH_EARNINGS_PAST


def _dividend_marker_glyph(record: Any) -> str:
    kind = str(getattr(record, "kind", "cash") or "cash")
    if kind == "stock_split":
        return GLYPH_SPLIT
    return EVENT_MARKER_GLYPH["dividend"]


def build_event_glyphs(
    view: Any,
    candles: Sequence[Any],
    *,
    blind: bool = False,
) -> list[EventGlyph]:
    """Project ``view`` onto the visible ``candles`` window.

    Events whose calendar day matches a visible bar produce in-pane
    glyphs anchored at that bar. Forward earnings beyond the visible
    window produce right-edge forward badges (``bar_index = -1``) so
    the trader gets a "next earnings in T-N days" cue without
    over-painting the chart.

    In blind mode the forward badge omits the absolute date (the
    gating layer already redacts it; this layer never sees the raw
    forward ts).

    Returns descriptors in the order: past dividends → past earnings
    → forward earnings (badges). Stable order means the renderer's
    z-order is determined by the iteration index, not by event ts.
    """
    out: list[EventGlyph] = []
    if view is None:
        return out

    past_d = list(getattr(view, "past_dividends", []) or [])
    past_e = list(getattr(view, "past_earnings", []) or [])
    forward_e = list(getattr(view, "forward_earnings", []) or [])
    forward_badges = list(getattr(view, "forward_badges", []) or [])

    # Precompute UTC-day → first-bar-index ONCE so each event below is an
    # O(1) lookup instead of an O(bars) re-scan. Turns projection from
    # O(events × bars) into O(bars + events) — a measured ~7-9 ms/render
    # saving on a busy symbol (many dividends/earnings in view).
    day_map = _build_day_index_map(candles)

    for d in past_d:
        ts_ms = int(getattr(d, "ex_ts", 0) or 0)
        idx = day_map.get(ts_ms // MS_PER_DAY, -1)
        if idx < 0:
            continue
        kind = str(getattr(d, "kind", "cash") or "cash")
        glyph = _dividend_glyph_for_kind(kind)
        out.append(EventGlyph(
            bar_index=idx,
            glyph_kind=glyph,
            tooltip=_dividend_tooltip(d),
            ts_ms=ts_ms,
            marker_glyph=_dividend_marker_glyph(d),
        ))

    for e in past_e:
        ts_ms = int(getattr(e, "ts", 0) or 0)
        idx = day_map.get(ts_ms // MS_PER_DAY, -1)
        if idx < 0:
            continue
        out.append(EventGlyph(
            bar_index=idx,
            glyph_kind=GLYPH_EARNINGS_PAST,
            tooltip=_earnings_tooltip(e, future=False),
            ts_ms=ts_ms,
            marker_glyph=_earnings_marker_glyph(e),
        ))

    for e in forward_e:
        ts_ms = int(getattr(e, "ts", 0) or 0)
        idx = day_map.get(ts_ms // MS_PER_DAY, -1)
        if idx < 0:
            continue
        out.append(EventGlyph(
            bar_index=idx,
            glyph_kind=GLYPH_EARNINGS_FORWARD,
            tooltip=_earnings_tooltip(e, future=True),
            ts_ms=ts_ms,
            marker_glyph=_earnings_marker_glyph(e),
        ))

    if forward_badges and not forward_e:
        # Only emit a right-edge badge when there's no in-pane glyph
        # for the same print; otherwise both would render and the
        # in-pane glyph carries strictly more information.
        nearest = min(forward_badges, key=lambda b: int(
            getattr(b, "trading_days_until", 9_999)))
        td = int(getattr(nearest, "trading_days_until", 0))
        when = str(getattr(nearest, "when", "") or "")
        if blind:
            tip = f"Earn T-{td} {when}".strip()
        else:
            tip = f"Next earnings in {td} trading days {when}".strip()
        out.append(EventGlyph(
            bar_index=-1,
            glyph_kind=GLYPH_EARNINGS_FORWARD,
            tooltip=tip,
            ts_ms=0,
        ))

    return out


__all__ = (
    "EventGlyph",
    "build_event_glyphs",
    "GLYPH_EARNINGS_PAST",
    "GLYPH_EARNINGS_FORWARD",
    "GLYPH_DIVIDEND",
    "GLYPH_SPECIAL_DIVIDEND",
    "GLYPH_SPLIT",
    "EVENT_MARKER_GLYPH",
)
