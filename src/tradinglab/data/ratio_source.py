"""Ratio pseudo-symbols — chart the per-bar quotient of two real symbols.

A *ratio symbol* is typed straight into the ticker box as ``NUM/DEN``
(e.g. ``AMD/NVDA`` to read intra-semiconductor leadership, ``XLF/SPY`` for
financials-vs-market sector RS, ``RSP/SPY`` for equal-weight-vs-cap-weight
breadth). The chart shows ``NUM`` divided by ``DEN`` bar-for-bar.

A handful of named **aliases** are also recognised for convenience — e.g.
``RSPSPY`` expands to ``RSP/SPY`` — see :data:`RATIO_SYMBOLS`. Aliases keep
the old shorthand working; the general ``NUM/DEN`` form is the primary path.

Resolution is **source-agnostic**: :func:`fetch_ratio` is handed the active
source's leg fetcher and recurses on the two legs, so a ratio symbol works
anywhere a normal ticker does — main chart, compare panel, companion
prefetch, watchlists, and (via its intraday legs) the synthetic today-bar
on the daily chart. The hook lives at the top of
:func:`tradinglab.data.yfinance_source.fetch_live_data`.

**Delimiter is ``/`` only.** It is the one separator that (a) ``disk_cache``
already sanitises out of cache filenames, (b) does not collide with real
symbols that use ``-`` / ``.`` (``BRK-B``, ``BRK.B``, ``BTC-USD``) or ``:``
(exchange prefixes / Windows-illegal). Ratio series are never persisted to
disk (see :func:`tradinglab.disk_cache.save`) — they recompute cheaply from
their legs, which DO cache individually.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from ..models import Candle

#: Named ratio aliases → ``(numerator, denominator)``. Keys are UPPERCASE and
#: separator-free shorthand the user can type instead of the full ``NUM/DEN``
#: form. The general ``NUM/DEN`` path needs no entry here; aliases exist only
#: for memorable gauges. Extend as desired, e.g. ``"QQQSPY": ("QQQ", "SPY")``.
RATIO_SYMBOLS: dict[str, tuple[str, str]] = {
    "RSPSPY": ("RSP", "SPY"),
}

#: The single delimiter that denotes a ratio in a typed ticker string.
RATIO_DELIMITER = "/"

#: Curated high-value ratio presets surfaced in the "New Ratio Chart" dialog,
#: as ``(numerator, denominator, description)``. Chosen for the market →
#: sector → stock relative-strength workflow (breadth, sector RS, pairs).
RATIO_PRESETS: tuple[tuple[str, str, str], ...] = (
    ("RSP", "SPY", "Breadth — equal-weight vs cap-weight S&P 500"),
    ("QQQ", "SPY", "Risk appetite — Nasdaq-100 vs S&P 500"),
    ("IWM", "SPY", "Small-cap risk appetite — Russell 2000 vs S&P 500"),
    ("SMH", "SPY", "Semis vs market"),
    ("XLF", "SPY", "Financials sector relative strength"),
    ("AMD", "SMH", "Stock vs sector — AMD vs semiconductors"),
    ("AMD", "NVDA", "Pairs — AMD vs NVDA leadership"),
    ("HYG", "IEF", "Risk-on / risk-off — high-yield vs Treasuries"),
)


def parse_ratio_symbol(ticker: str) -> tuple[str, str] | None:
    """Return ``(numerator, denominator)`` for a ratio symbol, else ``None``.

    Accepts two forms (case-insensitive, whitespace-tolerant):

    1. A registered **alias** from :data:`RATIO_SYMBOLS` (e.g. ``RSPSPY``).
    2. The general **``NUM/DEN``** form (e.g. ``AMD/NVDA``, ``amd / nvda``).

    Rules for the general form: exactly one ``/`` splitting into two
    non-empty legs, and **neither leg may itself be a ratio** (nested
    ``A/B/C`` or ``RSPSPY/SPY`` is rejected — this bounds the leg-fetch
    recursion). Returns ``None`` for any non-ratio ticker (the common case)
    so callers can cheaply gate on it before doing any work.
    """
    if not ticker:
        return None
    s = ticker.strip().upper()
    alias = RATIO_SYMBOLS.get(s)
    if alias is not None:
        return alias
    if RATIO_DELIMITER not in s:
        return None
    parts = s.split(RATIO_DELIMITER)
    if len(parts) != 2:
        return None  # nested A/B/C or stray delimiters
    num, den = parts[0].strip(), parts[1].strip()
    if not num or not den:
        return None
    # Legs must be plain symbols, not themselves ratios (e.g. an alias leg).
    if RATIO_SYMBOLS.get(num) is not None or RATIO_SYMBOLS.get(den) is not None:
        return None
    return (num, den)


def is_ratio_symbol(ticker: str) -> bool:
    """True iff ``ticker`` names a ratio pseudo-symbol (alias or ``NUM/DEN``)."""
    return parse_ratio_symbol(ticker) is not None


def canonical_ratio_symbol(ticker: str) -> str:
    """Return the canonical storage/key form of a typed ticker.

    Ratios normalise to uppercase, space-free ``NUM/DEN`` (so ``amd / nvda``
    and ``AMD/NVDA`` share one cache key / watchlist entry). Recognised
    aliases are preserved verbatim (``RSPSPY`` stays ``RSPSPY``). Non-ratio
    tickers are uppercased + stripped. Empty/``None`` input is returned
    unchanged.
    """
    if not ticker:
        return ticker
    s = ticker.strip().upper()
    legs = parse_ratio_symbol(s)
    if legs is None:
        return s
    if s in RATIO_SYMBOLS:
        return s
    return f"{legs[0]}{RATIO_DELIMITER}{legs[1]}"


def ratio_display_label(ticker: str) -> str:
    """Return a human label for a ratio (``"AMD / NVDA"``), else the input.

    Used for chart titles, watermarks, the window title and watchlist rows
    so a ratio reads unambiguously. Aliases expand to their legs
    (``RSPSPY`` → ``"RSP / SPY"``).
    """
    legs = parse_ratio_symbol(ticker)
    if legs is None:
        return ticker
    return f"{legs[0]} {RATIO_DELIMITER} {legs[1]}"


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
    "RATIO_DELIMITER",
    "RATIO_PRESETS",
    "RATIO_SYMBOLS",
    "canonical_ratio_symbol",
    "compute_ratio_candles",
    "fetch_ratio",
    "is_ratio_symbol",
    "parse_ratio_symbol",
    "ratio_display_label",
]
