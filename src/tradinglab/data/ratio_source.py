"""Ratio pseudo-symbols — chart the per-bar quotient of two real symbols.

A *ratio symbol* is typed straight into the ticker box as ``NUM/DEN``
(e.g. ``AMD/NVDA`` to read intra-semiconductor leadership, ``XLF/SPY`` for
financials-vs-market sector RS, ``RSP/SPY`` for equal-weight-vs-cap-weight
breadth). The chart shows ``NUM`` divided by ``DEN`` bar-for-bar.

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

**Two shapes share this syntax — they are NOT the same animal.**

``NUM/DEN`` where both legs are symbols is a *quotient ratio* (``AMD/NVDA``):
the per-bar OHLC path is unknowable, so its high/low is a widened envelope,
its volume is meaningless, and its bars are inner-joined (non-overlapping
bars are dropped).

``SYM/<number>`` is a *scaled symbol* (``^VIX/15.87``, ``SPX/10``): one real
instrument on a rescaled y-axis. Dividing by a positive constant is
order-preserving, so ``H/k`` IS the true high — no envelope approximation is
needed. There is no second calendar, so no bar is ever dropped, and the
underlying's volume stays entirely meaningful. Use :func:`is_quotient_ratio`
/ :func:`is_scaled_symbol` to tell them apart; :func:`is_ratio_symbol` stays
"either shape" so cache/persistence gating is unchanged.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from ..models import Candle

#: The single delimiter that denotes a ratio in a typed ticker string.
RATIO_DELIMITER = "/"

#: A leg is a scale CONSTANT iff the whole leg is a plain positive decimal.
#: Deliberately boring: no sign, no exponent, no thousands separator. A real
#: numeric-ish ticker essentially always carries a suffix (``0700.HK``,
#: ``BTC-USD``) that breaks this match, so the collision risk with a genuine
#: symbol is negligible for the vendors this app talks to.
_SCALE_CONSTANT_RE = re.compile(r"^\d+(?:\.\d+)?$")


def parse_ratio_symbol(ticker: str) -> tuple[str, str] | None:
    """Return ``(numerator, denominator)`` for a ratio symbol, else ``None``.

    Accepts the **``NUM/DEN``** form only (e.g. ``AMD/NVDA``, ``amd / nvda``),
    case-insensitive and whitespace-tolerant: exactly one ``/`` splitting
    into two non-empty legs. Nested forms (``A/B/C``) are rejected — this
    bounds the leg-fetch recursion. Returns ``None`` for any non-ratio
    ticker (the common case) so callers can cheaply gate on it before doing
    any work.
    """
    if not ticker:
        return None
    s = ticker.strip().upper()
    if RATIO_DELIMITER not in s:
        return None
    parts = s.split(RATIO_DELIMITER)
    if len(parts) != 2:
        return None  # nested A/B/C or stray delimiters
    num, den = parts[0].strip(), parts[1].strip()
    if not num or not den:
        return None
    return (num, den)


def is_ratio_symbol(ticker: str) -> bool:
    """True iff ``ticker`` names a ratio pseudo-symbol (``NUM/DEN``).

    Covers BOTH shapes — quotient ratios (``AMD/NVDA``) and scaled symbols
    (``^VIX/15.87``). Callers that care about the difference (volume-pane
    gating, rebase-to-100) must use :func:`is_quotient_ratio` /
    :func:`is_scaled_symbol` instead.
    """
    return parse_ratio_symbol(ticker) is not None


def parse_scale_constant(leg: str) -> float | None:
    """Return ``leg`` as a positive scale constant, else ``None``.

    Accepts a plain positive decimal only (``16``, ``15.87``, ``0.5``).
    Rejects ``0`` (divide-by-zero), negatives (a sign flip would invert the
    candle), scientific notation, thousands separators and a leading ``+`` —
    all of which add lexer surface for no discretionary-trading value.
    """
    if not leg:
        return None
    s = leg.strip()
    if not _SCALE_CONSTANT_RE.match(s):
        return None
    try:
        k = float(s)
    except ValueError:
        return None
    return k if k > 0 else None


def is_numeric_leg(leg: str) -> bool:
    """True if ``leg`` LOOKS numeric, whether or not it is a usable constant.

    Distinct from :func:`parse_scale_constant`, which also enforces
    positivity. A leg like ``0`` is numeric-shaped but unusable: it must be
    rejected outright rather than falling through and being fetched as a
    vendor ticker literally named ``"0"``.

    Public so UI error copy can tell "you meant a divisor but it's invalid"
    (``^VIX/0``) apart from "you meant two tickers" (``AMD/NVDA``) without
    re-implementing the number grammar.
    """
    return bool(leg) and bool(_SCALE_CONSTANT_RE.match(leg.strip()))


def scaled_symbol_parts(ticker: str) -> tuple[str, float] | None:
    """Return ``(base_symbol, divisor)`` for a scaled symbol, else ``None``.

    A scaled symbol is ``SYM/<positive number>`` — a real instrument on a
    rescaled axis (``^VIX/15.87``, ``SPX/10``). Only the DENOMINATOR may be a
    constant: ``100/VIX`` (constant numerator) is deliberately unsupported —
    an inverse needs the candle's high and low to SWAP, which is a different
    feature with a real misread risk. ``16/4`` (both constant) is likewise
    rejected.
    """
    legs = parse_ratio_symbol(ticker)
    if legs is None:
        return None
    num, den = legs
    if is_numeric_leg(num):
        return None  # constant numerator (incl. both-constant) — unsupported
    k = parse_scale_constant(den)
    if k is None:
        return None
    return (num, k)


def is_scaled_symbol(ticker: str) -> bool:
    """True iff ``ticker`` is ``SYM/<positive number>`` (e.g. ``^VIX/15.87``)."""
    return scaled_symbol_parts(ticker) is not None


def is_quotient_ratio(ticker: str) -> bool:
    """True iff ``ticker`` is a two-SYMBOL quotient (e.g. ``AMD/NVDA``).

    This — not :func:`is_ratio_symbol` — is the correct gate for the
    behaviours that only make sense for a true quotient: hiding the volume
    pane (a quotient has no meaningful volume; a scaled symbol has the
    underlying's real volume) and rebase-to-100 (which multiplies by a
    constant and therefore CANCELS a scale divisor exactly, silently undoing
    the whole point of ``^VIX/15.87``).
    """
    return is_ratio_symbol(ticker) and not is_scaled_symbol(ticker)


def base_symbol_of(ticker: str) -> str:
    """Return the underlying symbol of a scaled ticker, else ``ticker``.

    ``^VIX/15.87`` → ``^VIX``; ``AAPL/100`` → ``AAPL``. A quotient ratio or a
    plain ticker is returned unchanged. Used where a scaled chart should defer
    to its underlying — corporate-event lookup, for instance.
    """
    parts = scaled_symbol_parts(ticker)
    return parts[0] if parts is not None else ticker


def canonical_ratio_symbol(ticker: str) -> str:
    """Return the canonical storage/key form of a typed ticker.

    Ratios normalise to uppercase, space-free ``NUM/DEN`` (so ``amd / nvda``
    and ``AMD/NVDA`` share one cache key / watchlist entry). Non-ratio
    tickers are uppercased + stripped. Empty/``None`` input is returned
    unchanged.
    """
    if not ticker:
        return ticker
    s = ticker.strip().upper()
    legs = parse_ratio_symbol(s)
    if legs is None:
        return s
    return f"{legs[0]}{RATIO_DELIMITER}{legs[1]}"


def ratio_display_label(ticker: str) -> str:
    """Return a human label for a ratio (``"AMD / NVDA"``), else the input.

    Used for chart titles, watermarks, the window title and watchlist rows
    so a ratio reads unambiguously.
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


def compute_scaled_candles(
    candles: Sequence[Candle], divisor: float,
) -> list[Candle]:
    """Divide a real series' OHLC by a positive constant. Exact, lossless.

    Unlike :func:`compute_ratio_candles` this is **not** an approximation and
    performs **no** join:

    - Dividing by ``k > 0`` is order-preserving, so ``H/k`` is still the true
      high and ``L/k`` the true low. No envelope widening is needed (applying
      it would be a no-op under a monotone transform, but doing so would
      wrongly imply the bar's extremes are estimated).
    - A constant has no calendar, so **every** input bar survives — there is
      no second leg to inner-join against and therefore no silent bar loss.
    - ``volume`` is **preserved**: a scaled symbol is one real instrument, so
      its volume (and any volume-weighted study over it — VWAP scales by the
      same ``k``, RVOL is unchanged) stays entirely meaningful. This is the
      opposite of the quotient case, where volume is forced to ``0``.
    - ``session`` is carried through untouched.

    Returns ``[]`` for an empty input or a non-positive ``divisor``.
    """
    if not candles or divisor <= 0:
        return []
    k = float(divisor)
    return [
        Candle(
            date=c.date,
            open=c.open / k,
            high=c.high / k,
            low=c.low / k,
            close=c.close / k,
            volume=c.volume,
            session=getattr(c, "session", "regular"),
        )
        for c in candles
    ]


def fetch_ratio(
    ticker: str,
    interval: str,
    *,
    leg_fetcher: Callable[[str, str], Sequence[Candle] | None],
) -> list[Candle] | None:
    """Fetch + compute a ratio symbol's candles via ``leg_fetcher``.

    ``leg_fetcher`` is the active source's ``(ticker, interval) -> candles``
    callable. Routing:

    - **Scaled symbol** (``^VIX/15.87``) — fetches the ONE real leg and
      applies :func:`compute_scaled_candles`. The constant is never handed to
      ``leg_fetcher`` (there is nothing to fetch), which also means range
      kwargs can't leak to a non-existent second leg.
    - **Quotient ratio** (``AMD/NVDA``) — both legs are fetched from the same
      source at the same interval and combined by
      :func:`compute_ratio_candles`.

    Returns ``None`` when ``ticker`` isn't a ratio symbol, when a leg fails /
    is empty, or when the shape is an unsupported constant-numerator form
    (``100/VIX``, ``16/4``) — so the caller's normal ``None``-handling (status
    message, disk fallback) applies unchanged.
    """
    legs = parse_ratio_symbol(ticker)
    if legs is None:
        return None
    num_sym, den_sym = legs
    # A numeric-LOOKING leg is never a vendor ticker. Constant numerators
    # (``100/VIX``, ``16/4``) are an unsupported shape, and an unusable
    # denominator (``^VIX/0``) must fail here rather than fall through and
    # have the source asked for a symbol literally named "0".
    if is_numeric_leg(num_sym):
        return None
    if is_numeric_leg(den_sym):
        divisor = parse_scale_constant(den_sym)
        if divisor is None:
            return None
        base = leg_fetcher(num_sym, interval)
        if not base:
            return None
        return compute_scaled_candles(base, divisor)
    num = leg_fetcher(num_sym, interval)
    if not num:
        return None
    den = leg_fetcher(den_sym, interval)
    if not den:
        return None
    return compute_ratio_candles(num, den)


__all__ = [
    "RATIO_DELIMITER",
    "base_symbol_of",
    "canonical_ratio_symbol",
    "compute_ratio_candles",
    "compute_scaled_candles",
    "fetch_ratio",
    "is_numeric_leg",
    "is_quotient_ratio",
    "is_ratio_symbol",
    "is_scaled_symbol",
    "parse_ratio_symbol",
    "parse_scale_constant",
    "ratio_display_label",
    "scaled_symbol_parts",
]
