"""Synthetic ratio pseudo-symbols (e.g. ``RSPSPY`` = RSP / SPY).

A *ratio symbol* charts the per-bar quotient of two real symbols. The
canonical example is **RSPSPY** — the equal-weight S&P 500 ETF ``RSP``
divided by the cap-weight ``SPY`` — a standard macro / breadth gauge:
rising ⇒ broad participation (equal-weight leading), falling ⇒ mega-cap
concentration.

Resolution is **source-agnostic**: :func:`fetch_ratio` is handed the active
source's leg fetcher and recurses on the two legs, so a ratio symbol works
anywhere a normal ticker does — main chart, compare panel, companion
prefetch, watchlists, and (via its intraday legs) the synthetic today-bar
on the daily chart. The hook lives at the top of
:func:`tradinglab.data.yfinance_source.fetch_live_data`.

Adding a new gauge is a one-line edit to :data:`RATIO_SYMBOLS` — no other
wiring is needed.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from ..models import Candle

#: Registered ratio pseudo-symbols → ``(numerator, denominator)``. Keys are
#: UPPERCASE and separator-free (matching what the user types in the ticker
#: box). Extend with other breadth/macro gauges as needed, e.g.
#: ``"QQQSPY": ("QQQ", "SPY")`` or ``"IWMSPY": ("IWM", "SPY")``.
RATIO_SYMBOLS: dict[str, tuple[str, str]] = {
    "RSPSPY": ("RSP", "SPY"),
}


def parse_ratio_symbol(ticker: str) -> tuple[str, str] | None:
    """Return ``(numerator, denominator)`` for a registered ratio symbol.

    Case-insensitive + whitespace-tolerant. Returns ``None`` for any
    non-ratio ticker (the overwhelming common case) so callers can cheaply
    gate on it before doing any work.
    """
    if not ticker:
        return None
    return RATIO_SYMBOLS.get(ticker.strip().upper())


def is_ratio_symbol(ticker: str) -> bool:
    """True iff ``ticker`` names a registered ratio pseudo-symbol."""
    return parse_ratio_symbol(ticker) is not None


def compute_ratio_candles(
    numerator: Sequence[Candle], denominator: Sequence[Candle],
) -> list[Candle]:
    """Per-bar component-wise quotient of two candle series.

    Bars are inner-joined on timestamp — only dates present in BOTH legs
    contribute. For each shared bar the OHLC is the component quotient
    (``O = numO/denO`` …); ``H`` / ``L`` are then widened to the
    ``max`` / ``min`` of the four quotients so the result is always a valid
    candle (``H ≥ O,C ≥ L``) even though the true intra-bar ratio path is
    unknowable — this matches how charting platforms render symbol ratios.

    - Volume is meaningless for a ratio and set to ``0``.
    - Bars whose denominator has any non-positive OHLC component are
      skipped (avoids divide-by-zero / sign flips).
    - ``session`` is carried from the numerator bar (so the daily
      today-bar synthesiser's regular-session filter still works).
    """
    den_by_ts: dict[object, Candle] = {}
    for c in denominator:
        try:
            den_by_ts[c.date] = c
        except Exception:  # noqa: BLE001
            continue
    out: list[Candle] = []
    for n in numerator:
        d = den_by_ts.get(n.date)
        if d is None:
            continue
        if d.open <= 0 or d.high <= 0 or d.low <= 0 or d.close <= 0:
            continue
        o = n.open / d.open
        h = n.high / d.high
        lo = n.low / d.low
        c = n.close / d.close
        out.append(Candle(
            date=n.date,
            open=o,
            high=max(o, h, lo, c),
            low=min(o, h, lo, c),
            close=c,
            volume=0,
            session=getattr(n, "session", "regular"),
        ))
    return out


def fetch_ratio(
    ticker: str,
    interval: str,
    *,
    leg_fetcher: Callable[[str, str], Sequence[Candle] | None],
) -> list[Candle] | None:
    """Fetch + compute a ratio symbol's candles via ``leg_fetcher``.

    ``leg_fetcher`` is the active source's ``(ticker, interval) -> candles``
    callable; both legs are fetched from the same source. Returns ``None``
    when ``ticker`` isn't a ratio symbol, or when either leg fails / is
    empty — so the caller's normal ``None``-handling (status message, disk
    fallback) applies unchanged.
    """
    legs = parse_ratio_symbol(ticker)
    if legs is None:
        return None
    num_sym, den_sym = legs
    num = leg_fetcher(num_sym, interval)
    if not num:
        return None
    den = leg_fetcher(den_sym, interval)
    if not den:
        return None
    return compute_ratio_candles(num, den)


__all__ = [
    "RATIO_SYMBOLS",
    "compute_ratio_candles",
    "fetch_ratio",
    "is_ratio_symbol",
    "parse_ratio_symbol",
]
