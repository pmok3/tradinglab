"""Synthesize today's partial daily bar from cached intraday data.

The 1d (and 1wk/1mo) chart's "most recent bar" lags the live session by up
to one day because most data providers (yfinance, Schwab, Polygon) don't
emit today's daily bar until after the close. Mid-session a user sees
"everything up to yesterday" on the daily chart, while the 5m chart
already shows the in-progress bar.

This module fixes that by upsampling whatever intraday candles we already
have cached (5m, 1m, …) into a synthesized "today" daily candle and
appending it to the daily series before render. Audit tag:
``daily-today-upsample``.

The synthesis only mutates a working copy — the raw ``_full_cache`` entry
keeps the provider's truthful (lagged) data, so the next provider fetch
that actually contains today simply overwrites our synthetic bar at the
boundary instead of leaving stale state behind.

Scope: currently 1d only. 1wk / 1mo would need additional aggregation
(week-to-date / month-to-date over a mix of intraday + cached daily
bars) and are deferred — most discretionary traders glance at 1d
mid-session and 1wk / 1mo only after the close.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone

from ..models import Candle

logger = logging.getLogger(__name__)

#: Resolution order — finest intraday interval first. The synthesis uses
#: the highest-resolution intraday data available in cache (1m > 2m > 5m
#: > 15m > 30m > 1h) so the running OHLC most closely matches what the
#: user sees on their preferred intraday chart.
_INTRADAY_RESOLUTION_ORDER: tuple[str, ...] = (
    "1m", "2m", "5m", "15m", "30m", "1h",
)

#: Daily-class intervals this module synthesises today's bar for. 1wk
#: and 1mo intentionally excluded for now — see module docstring.
SUPPORTED_INTERVALS: frozenset[str] = frozenset({"1d"})


def _et_zoneinfo():
    """Return ``ZoneInfo('America/New_York')`` or None on missing tzdata."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        return None


def _et_date_of(dt: datetime) -> date:
    """Return the US-Eastern calendar date for an OHLCV timestamp.

    - tz-aware → convert to ET via :mod:`zoneinfo` (falls back to a
      naive ``-5h`` offset when tzdata is missing — conservative).
    - tz-naive → return ``dt.date()`` directly. Tz-naive intraday
      candles in this codebase are treated as already-in-ET (see
      ``models.spec.md``).
    """
    if dt.tzinfo is None:
        return dt.date()
    et = _et_zoneinfo()
    if et is not None:
        return dt.astimezone(et).date()
    # tzdata-less fallback: assume UTC and shift -5h. Off by one for
    # ~10% of the year (EDT vs EST), but only matters across midnight
    # ET which RTH never straddles.
    return (dt.astimezone(timezone.utc) - timedelta(hours=5)).date()


def _today_et(now: datetime | None = None) -> date:
    """Return today's US-Eastern calendar date."""
    if now is not None:
        return _et_date_of(now if now.tzinfo else now.replace(tzinfo=timezone.utc))
    et = _et_zoneinfo()
    if et is not None:
        return datetime.now(et).date()
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def synthesize_today_daily_candle(
    intraday_candles: Sequence[Candle],
    *,
    today_et: date | None = None,
    sessions: frozenset[str] = frozenset({"regular"}),
) -> Candle | None:
    """Aggregate today's intraday bars into a single synthetic daily candle.

    ``O`` = first matched bar's open; ``H`` = max over matched highs;
    ``L`` = min over matched lows; ``C`` = last matched bar's close;
    ``V`` = sum of matched volumes. The candle's ``date`` preserves the
    first matched bar's timestamp so any hover lookup keyed on the
    timestamp still resolves a real intraday bar — the synthesis is
    invisible to event/glyph code that joins on the date.

    Returns ``None`` when no intraday bars match today's ET date for the
    requested ``sessions`` set (typical before 09:30 ET, or on weekends).
    """
    if not intraday_candles:
        return None
    if today_et is None:
        today_et = _today_et()
    matches: list[Candle] = []
    for c in intraday_candles:
        if c.session not in sessions:
            continue
        if _et_date_of(c.date) != today_et:
            continue
        matches.append(c)
    if not matches:
        return None
    open_v = matches[0].open
    close_v = matches[-1].close
    high_v = max(c.high for c in matches)
    low_v = min(c.low for c in matches)
    vol_v = sum(int(c.volume) for c in matches)
    return Candle(
        date=matches[0].date,
        open=open_v,
        high=high_v,
        low=low_v,
        close=close_v,
        volume=int(vol_v),
        session="regular",
    )


def find_best_intraday_source(
    full_cache: Mapping,
    *,
    source: str,
    symbol: str,
) -> list[Candle] | None:
    """Pick the highest-resolution intraday candles cached for ``symbol``.

    Iterates :data:`_INTRADAY_RESOLUTION_ORDER` finest-first and returns
    the first non-empty cache entry. Returns ``None`` when no intraday
    interval is cached for the symbol — that's the cue for the caller to
    schedule a 5m prefetch so a synthetic bar can land on the next render.
    """
    for iv in _INTRADAY_RESOLUTION_ORDER:
        bars = full_cache.get((source, symbol, iv))
        if bars:
            return list(bars)
    return None


def upsample_daily_with_today(
    daily_candles: Sequence[Candle] | None,
    *,
    intraday_candles: Sequence[Candle] | None,
    today_et: date | None = None,
) -> list[Candle]:
    """Append (or overwrite) today's synthetic daily candle.

    - When the daily series' most recent bar's ET date already equals
      today's, overwrite it (the provider has emitted a partial bar
      that we improve with finer-grained running OHLCV).
    - Otherwise append the synthetic bar.
    - No-op when ``intraday_candles`` is empty / yields no match.

    The returned list is always a fresh copy — callers can use it
    without worrying about mutating shared cache state. Audit
    ``daily-today-upsample``.
    """
    base = list(daily_candles or [])
    if not intraday_candles:
        return base
    synth = synthesize_today_daily_candle(
        intraday_candles, today_et=today_et,
    )
    if synth is None:
        return base
    synth_date = _et_date_of(synth.date)
    if base and _et_date_of(base[-1].date) == synth_date:
        base[-1] = synth
    else:
        base.append(synth)
    return base


__all__ = [
    "SUPPORTED_INTERVALS",
    "find_best_intraday_source",
    "synthesize_today_daily_candle",
    "upsample_daily_with_today",
]
