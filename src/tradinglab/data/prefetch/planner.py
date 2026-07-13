"""Per-source band planning for the prefetch scheduler.

"**band = one maximal API request**" (Decision 8). A ``WindowPlanner`` maps
``(symbol, interval, band_index)`` to a :class:`FetchWindow` request descriptor,
newest-first, so each band costs exactly one rate-limiter token while pulling the
most bars possible.

Two provider styles:

* :class:`PeriodWindowPlanner` (yfinance) — no pagination; band 0 is the
  interval's max *trailing* period, and there is no deeper intraday band
  (yfinance only serves a trailing window from *now*), so ``band(>=1) -> None``.
* :class:`RangeWindowPlanner` (Alpaca) — band 0 fetches the most recent max page
  (``limit`` bars); band *k* steps back via ``end = oldest_ts`` reached so far.
  The scheduler stops deepening when a fetch returns no older bars (the planner
  itself never signals exhaustion by index).

Pure — no Tk / IO / network. The scheduler translates a :class:`FetchWindow`
into the actual fetcher / ``fetch_range`` call at dispatch time. See
``PREFETCH_SCHEDULER_DESIGN.md`` §4.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Alpaca bars API max page size (bars per request).
ALPACA_MAX_PAGE = 10_000

#: yfinance max trailing period per interval. Its intraday history is capped and
#: only served as a trailing window from *now*, so there are no deeper bands.
_YF_MAX_PERIOD: dict[str, str] = {
    "1m": "7d",
    "2m": "60d", "5m": "60d", "15m": "60d", "30m": "60d", "90m": "60d",
    "60m": "730d", "1h": "730d",
}
#: 1d / 1wk / 1mo / unknown intervals -> full history in a single call.
_YF_DEFAULT_PERIOD = "max"


@dataclass(frozen=True)
class FetchWindow:
    """One request descriptor (one band). ``kind`` discriminates the fields."""

    interval: str
    kind: str = "period"          # "period" | "range"
    period: str | None = None     # period-style (yfinance)
    start: float | None = None    # range-style (Alpaca), epoch seconds
    end: float | None = None
    limit: int | None = None


class PeriodWindowPlanner:
    """yfinance-style planner: one max-period request, no deeper bands."""

    def __init__(
        self,
        period_table: dict[str, str] | None = None,
        default_period: str = _YF_DEFAULT_PERIOD,
    ) -> None:
        self._table = dict(_YF_MAX_PERIOD if period_table is None else period_table)
        self._default = default_period

    def band(
        self, symbol: str, interval: str, band_index: int,
        *, oldest_ts: float | None = None,
    ) -> FetchWindow | None:
        if band_index != 0:
            return None
        period = self._table.get(interval, self._default)
        return FetchWindow(interval=interval, kind="period", period=period)


class RangeWindowPlanner:
    """Alpaca-style planner: newest max page first, step back via ``oldest_ts``.

    **One-HTTP-page-per-band contract** (principal-SWE review): the fetch
    primitive this drives must return the *most recent* ``limit`` bars whose
    timestamp is **strictly before** ``end`` (i.e. ``sort=desc`` + ``end`` +
    ``limit``), which is **one HTTP request = one rate-limiter token**. Under
    that primitive the ``end = oldest_ts`` step-back is exactly correct: band 0
    fetches the latest page, band *k* fetches the page ending at band *k-1*'s
    oldest bar. ``end`` is treated as **exclusive**, so consecutive bands don't
    overlap. The scheduler stops deepening when a fetch yields no older bars
    (``oldest_ts`` fails to advance); the planner itself never signals
    exhaustion by index.
    """

    def __init__(self, max_page: int = ALPACA_MAX_PAGE) -> None:
        self._max_page = int(max_page)

    def band(
        self, symbol: str, interval: str, band_index: int,
        *, oldest_ts: float | None = None,
    ) -> FetchWindow | None:
        if band_index < 0:
            return None
        if band_index == 0:
            return FetchWindow(
                interval=interval, kind="range", end=None, limit=self._max_page,
            )
        if oldest_ts is None:
            return None  # need the previous band's boundary to step back
        return FetchWindow(
            interval=interval, kind="range",
            end=float(oldest_ts), limit=self._max_page,
        )


def planner_for(*, supports_range: bool) -> PeriodWindowPlanner | RangeWindowPlanner:
    """Pick a planner from a source's ``supports_range`` capability."""
    return RangeWindowPlanner() if supports_range else PeriodWindowPlanner()


__all__ = [
    "ALPACA_MAX_PAGE", "FetchWindow",
    "PeriodWindowPlanner", "RangeWindowPlanner", "planner_for",
]
