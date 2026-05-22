"""yfinance-backed earnings + dividends fetcher.

Mirrors :mod:`tradinglab.data.yfinance_source` in posture:

* No retries — provider errors, empty frames, and import failures
  collapse to ``None``. The cache layer papers over flakiness.
* Tolerant of yfinance column schema drift via
  :mod:`events.normalize`. See that module for the variant matrix
  (``EPS Estimate`` / ``Reported EPS`` / ``EPS Actual`` / ``Estimate``,
  ``Revenue Estimate`` / ``Reported Revenue`` / ``Revenue``).
* Returns trading-day-floored UTC midnight ms for ``ts``; the BMO/AMC
  slot is derived from the tz-aware minute only as a heuristic. The
  slot enum is authoritative; the wall-clock minute is not preserved.

The fetcher itself is now a thin shell — it owns the SDK import, the
two ``Ticker`` method calls (:attr:`Ticker.earnings_dates`,
:attr:`Ticker.actions`), and the merging of the resulting record
lists into one :class:`EventBundle`. Everything else is in
:mod:`events.normalize`.
"""

from __future__ import annotations

import datetime as _dt
import logging

from .base import EventBundle
from .normalize import normalize_actions_df, normalize_earnings_df

logger = logging.getLogger(__name__)

_EPOCH_UTC = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)

# One-shot guard so a missing-lxml install doesn't spam the log on
# every watchlist tick. yfinance.Ticker.earnings_dates internally
# parses HTML via ``pandas.read_html`` which needs lxml (or html5lib);
# without it the call raises ``ImportError`` and we'd otherwise
# silently emit empty earnings forever. Surfaced as a single WARNING
# the first time we see it so the user can ``pip install lxml`` —
# fall-through behavior (empty earnings) is preserved either way.
# Audit ``next-earn-lxml``.
_logged_earnings_dates_failure: set[str] = set()


def fetch_yfinance_events(ticker: str) -> EventBundle | None:
    """Fetch earnings + dividends + splits for ``ticker`` via yfinance.

    Returns ``None`` on import failure, network error, or empty
    response. Partial success (earnings present but no dividends, or
    vice versa) returns an :class:`EventBundle` with the empty list
    for the missing axis — callers can still render the half they have.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return None

    try:
        import yfinance as yf  # local import: keeps app start cheap
    except ImportError:
        return None

    try:
        tk = yf.Ticker(sym)
    except Exception:  # noqa: BLE001
        return None

    # ---- earnings -----------------------------------------------------
    try:
        earnings_df = tk.earnings_dates  # may be None or empty
    except Exception as exc:  # noqa: BLE001
        earnings_df = None
        # First-occurrence-only log so a chronic install issue (most
        # commonly: missing lxml) shows up once instead of N times per
        # watchlist tick. ``exc.__class__.__name__`` is enough context
        # for the user without dragging the full traceback into the
        # log on every fetch.
        kind = type(exc).__name__
        if kind not in _logged_earnings_dates_failure:
            _logged_earnings_dates_failure.add(kind)
            logger.warning(
                "yfinance Ticker.earnings_dates raised %s (%s); "
                "the watchlist 'Next Earn' column will stay blank "
                "until this resolves. If the message mentions lxml, "
                "run: pip install lxml",
                kind, exc,
            )
    earnings_records = normalize_earnings_df(
        earnings_df, symbol=sym, source="yfinance")

    # ---- dividends + splits (via Ticker.actions) ----------------------
    try:
        actions_df = tk.actions
    except Exception:  # noqa: BLE001
        actions_df = None
    dividends_records = normalize_actions_df(
        actions_df, symbol=sym, source="yfinance")

    if not earnings_records and not dividends_records:
        return None

    today_ms = int((_dt.datetime.now(tz=_dt.timezone.utc) - _EPOCH_UTC)
                   .total_seconds() * 1000)
    return EventBundle(
        symbol=sym,
        earnings=earnings_records,
        dividends=dividends_records,
        fetched_at=today_ms,
    )


__all__ = ("fetch_yfinance_events",)
