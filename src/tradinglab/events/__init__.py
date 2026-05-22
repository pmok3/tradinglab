"""Historical earnings & dividend events.

A *sibling* of :mod:`tradinglab.data`: data fetches OHLCV bars,
events fetches sparse point-in-time records (earnings prints, ex-dividend
dates, splits, spin-offs). The subsystem stays a strict outsider of the
:mod:`tradinglab.backtest` engine — events are **ambient context**,
not part of the reproducible :class:`SessionResult`. See
``backtest/actions.py`` for the engine-output corporate-action records
that ARE persisted.

Public API
----------
    EVENT_SOURCES         — registry {name: fetcher}
    EventFetcher          — the (ticker) -> EventBundle callable type
    register_event_source — imperative registration helper
    EarningsRecord, DividendRecord, EventBundle  — canonical record shapes
    fetch_yfinance_events — yfinance-backed fetcher
    fetch_synthetic_events — deterministic offline fetcher (for tests)
    EventsView, events_visible_for  — sandbox gating + blind redaction

Architectural rules
-------------------
* Records are immutable in the past (printed earnings, applied ex-div)
  and mutable in the future (date may be rescheduled, surprise may be
  backfilled). The cache layer enforces immutability for past records.
* Bundle ts values are UTC ms-since-epoch ``int``. Display tz is
  applied by ``formatting.format_dt`` at the render boundary.
* Earnings ts is floored to the trading-day UTC midnight; the
  ``EarningsRecord.when`` enum carries the BMO/AMC/DMH slot
  authoritatively. yfinance's tz-aware minute is *not* trusted for
  the slot — providers drift.
"""

from .base import (
    EVENT_SOURCES,
    DividendRecord,
    EarningsRecord,
    EventBundle,
    EventFetcher,
    register_event_source,
)
from .gating import EventsView, events_visible_for
from .synthetic_events import fetch_synthetic_events
from .yfinance_events import fetch_yfinance_events

# Built-ins. Mirror the data/__init__.py pattern: yfinance first
# (default), synthetic second (always available for tests). Future
# Schwab / Polygon / Alpaca event providers register conditionally
# on credentials, identical to data/__init__.py lines 56–63.
register_event_source("yfinance", fetch_yfinance_events)
register_event_source("synthetic", fetch_synthetic_events)


__all__ = [
    "EVENT_SOURCES",
    "EventFetcher",
    "EarningsRecord",
    "DividendRecord",
    "EventBundle",
    "register_event_source",
    "fetch_yfinance_events",
    "fetch_synthetic_events",
    "EventsView",
    "events_visible_for",
]
