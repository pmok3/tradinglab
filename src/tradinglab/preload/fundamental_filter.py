"""Pure-function fundamental filter for the universe preload dialog.

Background
----------

The original Prepare-Universe dialog (``gui/universe_prepare_dialog.py``)
let the user pick a universe (S&P 500, Nasdaq-100, or a watchlist) and
preload it. For a focused intraday discretionary trader this scope is
often too broad — the user typically only wants to scan tickers with
enough liquidity to fill, and enough price-per-share to be worth
charting. Without a programmatic filter the user is forced to maintain
hand-curated watchlists, which goes stale fast.

This module is the pure-function backbone of the fundamental-filter
feature: given a 1d-bar series for a symbol, decide whether it passes
the user-defined :class:`FundamentalFilter` criteria. The GUI dialog
fetches the bars (cache-first; network fallback) in a worker thread
and calls :func:`passes_fundamental_filter` per symbol. All threading
and Tk-marshalling concerns live in the dialog — this module is pure
synchronous business logic so unit tests don't need an event loop.

Criteria
--------

* ``min_avg_volume_millions``  — minimum mean daily volume over the
  last ``lookback_days`` bars, expressed in millions of shares.
* ``min_close``                — minimum last-bar closing price (USD).
* ``max_close``                — maximum last-bar closing price (USD).
* ``lookback_days``            — number of trailing daily bars to
  average over for the volume criterion. Defaults to 20. A symbol
  with fewer than ``lookback_days`` bars fails the volume check
  (insufficient history is treated as a fail, not a pass — we'd
  rather under-include than over-include).

A criterion is "off" when its value is ``None``. A filter with every
criterion off accepts every symbol (the caller should detect this
case and skip the pre-pass entirely; :func:`is_filter_active` is a
convenience predicate for that).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..models import Candle


@dataclass(frozen=True)
class FundamentalFilter:
    """Immutable filter spec built from the dialog's form fields.

    Every field is ``None`` to mean "no constraint on this dimension"
    so that partial filters (e.g. price-only or volume-only) are
    expressible. ``lookback_days`` is always concrete because it is
    only consulted when the volume criterion is active.
    """

    min_avg_volume_millions: float | None = None
    min_close: float | None = None
    max_close: float | None = None
    lookback_days: int = 20


def is_filter_active(spec: FundamentalFilter) -> bool:
    """Return True iff at least one criterion is set."""
    return (
        spec.min_avg_volume_millions is not None
        or spec.min_close is not None
        or spec.max_close is not None
    )


def passes_fundamental_filter(
    daily_bars: Sequence[Candle],
    spec: FundamentalFilter,
) -> bool:
    """Decide whether one symbol's daily-bar series passes the filter.

    ``daily_bars`` is expected to be sorted ascending by time. Empty
    or ``None`` input fails every active criterion (no history → no
    pass; the caller should treat the symbol as filtered-out).
    """
    if not daily_bars:
        return not is_filter_active(spec)

    last = daily_bars[-1]
    last_close = float(last.close)

    if spec.min_close is not None and last_close < float(spec.min_close):
        return False
    if spec.max_close is not None and last_close > float(spec.max_close):
        return False

    if spec.min_avg_volume_millions is not None:
        lookback = max(1, int(spec.lookback_days))
        recent = daily_bars[-lookback:]
        if len(recent) < lookback:
            # Insufficient history for the requested lookback — fail
            # the volume gate. Better to under-include than to
            # over-include a thin-data ticker that *might* be liquid.
            return False
        total = 0.0
        for bar in recent:
            total += float(bar.volume)
        avg_vol = total / float(len(recent))
        threshold = float(spec.min_avg_volume_millions) * 1_000_000.0
        if avg_vol < threshold:
            return False

    return True


def filter_symbols(
    symbols: Iterable[str],
    bars_lookup: callable,  # type: ignore[valid-type]
    spec: FundamentalFilter,
) -> list[str]:
    """Apply the filter to an iterable of symbols.

    ``bars_lookup(symbol) -> Optional[List[Candle]]`` is injected so
    the caller can decide whether to use disk cache, the live
    fetcher, or a fake. Returns the symbols that pass, in input
    order.

    This helper is provided for tests / scripts; the GUI dialog
    interleaves the lookup with progress reporting and so calls
    :func:`passes_fundamental_filter` directly.
    """
    out: list[str] = []
    if not is_filter_active(spec):
        return [s for s in symbols]
    for sym in symbols:
        bars = bars_lookup(sym)
        if bars is None:
            continue
        if passes_fundamental_filter(bars, spec):
            out.append(sym)
    return out
