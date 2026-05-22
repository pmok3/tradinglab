"""Event-source protocol, canonical record types, and registry.

The data flow mirrors :mod:`tradinglab.data` to keep cognitive load
low — but the *types* are deliberately different (sparse point-in-time
records, not dense OHLCV series), so this layer does not extend or
sub-type the candle abstractions.

To add a new provider:

1. Create ``tradinglab/events/<name>_events.py`` exporting a
   function with the :data:`EventFetcher` signature.
2. Call :func:`register_event_source` at module import time (or have
   ``tradinglab/events/__init__.py`` register conditionally based on
   credentials).
3. Import the module from ``events/__init__.py`` so it loads on package
   import.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------

@dataclass
class EarningsRecord:
    """A single earnings print (past or upcoming).

    ``ts`` is the trading-day UTC midnight in ms-since-epoch. The
    actual minute the print landed (16:05 ET, 07:00 ET, …) is NOT
    stored — instead, :attr:`when` enumerates the slot. This avoids
    yfinance schema drift around the tz-aware minute and matches the
    trader's mental model ("MSFT prints Thursday AMC").

    ``eps_estimate`` / ``eps_actual`` and the revenue counterparts use
    NaN to mean "unknown / not yet released". A future earnings row
    has NaN actuals and (usually) finite estimates; a back-dated row
    with both NaN means a missing fundamental (provider didn't ship).
    """
    ts: int
    symbol: str
    when: str            # "BMO" | "AMC" | "DMH" | ""
    eps_estimate: float = math.nan
    eps_actual: float = math.nan
    revenue_estimate: float = math.nan
    revenue_actual: float = math.nan
    source: str = ""

    @property
    def is_future(self) -> bool:
        """True iff the actual EPS hasn't been released yet.

        Note: this is a record-shape predicate (NaN-vs-finite), NOT a
        clock comparison. Sandbox gating compares against
        ``current_clock_ts`` separately — see :mod:`events.gating`.
        """
        return math.isnan(self.eps_actual)

    @property
    def surprise_pct(self) -> float:
        """Signed EPS surprise as a percentage. NaN if either side is NaN
        or the estimate is zero."""
        est = float(self.eps_estimate)
        act = float(self.eps_actual)
        if math.isnan(est) or math.isnan(act) or est == 0.0:
            return math.nan
        return (act - est) / abs(est) * 100.0


@dataclass
class DividendRecord:
    """A single ex-dividend / split / spin-off event.

    ``ex_ts`` is the ex-date floored to UTC midnight ms-since-epoch.
    ``amount`` is per-share cash flow in the security's quote currency
    (USD for the v1 universe). For non-cash events (splits, spinoffs)
    ``amount`` semantics differ — see ``kind``.

    ``kind`` values:
      * ``"cash"``         — regular quarterly / monthly dividend. Use
                              ``amount`` directly.
      * ``"special"``      — one-off / special / liquidating dividend.
                              Same shape as cash but flagged for
                              proximity auto-tagging.
      * ``"stock_split"``  — forward or reverse split. ``ratio_num`` /
                              ``ratio_den`` are authoritative;
                              ``amount`` is undefined (NaN).
      * ``"spinoff"``      — parent receives ``amount`` of cash-
                              equivalent value at ex-date (v1 collapses
                              spin-offs to a cash credit per the user's
                              Q10 answer; child position not
                              materialised).
    """
    ex_ts: int
    symbol: str
    amount: float = 0.0
    kind: str = "cash"
    pay_ts: int = 0
    declared_ts: int = 0
    ratio_num: int = 1
    ratio_den: int = 1
    source: str = ""

    @property
    def is_cash_event(self) -> bool:
        return self.kind in ("cash", "special", "spinoff")

    @property
    def is_split(self) -> bool:
        return self.kind == "stock_split"


@dataclass
class EventBundle:
    """A single-symbol bundle returned from an :data:`EventFetcher`.

    Earnings and dividends ride side-by-side because providers (yfinance
    especially) return them from sibling methods on the same Ticker;
    splitting the fetcher signature would double the round-trip count
    for no benefit.

    Both lists are sorted ascending by their primary timestamp axis.
    Callers may assume this; producers must enforce it.

    ``fetched_at`` is UTC ms-since-epoch of the most recent provider
    call. Used by :mod:`events.cache` to TTL the *mutable* zone
    (upcoming earnings dates can move) while leaving the immutable
    zone (past prints) cached indefinitely.
    """
    symbol: str
    earnings: List[EarningsRecord] = field(default_factory=list)
    dividends: List[DividendRecord] = field(default_factory=list)
    fetched_at: int = 0

    def __post_init__(self) -> None:
        self.earnings.sort(key=lambda r: r.ts)
        self.dividends.sort(key=lambda r: r.ex_ts)


# ---------------------------------------------------------------------------
# Protocol + registry
# ---------------------------------------------------------------------------

# An event source fetcher takes a ticker symbol and returns an
# EventBundle (possibly with empty lists) on success, or None on
# failure. Treating provider errors, empty frames, and import failures
# identically mirrors :data:`data.base.DataFetcher`.
EventFetcher = Callable[[str], Optional[EventBundle]]


# Global registry. Populated by submodules at import time. Dict order
# mirrors :data:`data.base.DATA_SOURCES`; the first entry is the
# default selection for ``defaults.get("events_source")``.
EVENT_SOURCES: Dict[str, EventFetcher] = {}


def register_event_source(name: str, fetcher: EventFetcher) -> None:
    """Register a new event source under ``name``.

    Idempotent: repeat registrations overwrite. This is intentional so
    smoke tests can stub real sources by calling
    ``register_event_source("yfinance", fake)``.
    """
    EVENT_SOURCES[name] = fetcher


__all__ = (
    "EarningsRecord",
    "DividendRecord",
    "EventBundle",
    "EventFetcher",
    "EVENT_SOURCES",
    "register_event_source",
)
