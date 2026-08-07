"""Schwab LEVELONE quote source — the breadth adapter.

Implements :class:`~tradinglab.streaming.quotes.QuoteSource` against
Schwab's ``LEVELONE_EQUITIES`` streamer service, which already carries
everything a heatmap, a live scanner, or a watchlist percent column
needs: last price, the **previous session's official close**, cumulative
consolidated day volume, and the day's high/low.

Why this is not built on :class:`~tradinglab.streaming.schwab.SchwabStreamSource`
------------------------------------------------------------------------------

It shares that class's *connection* but not its *shape*. The bar source
creates a ``MinuteBarBuilder`` per subscription to synthesize 1-minute
OHLCV from ticks; pointing it at 500 symbols would stand up 500
aggregators and 500 REST seed lookups to reconstruct a number the wire
already sends. Quotes skip all of it.

Schwab permits **one streamer connection per user**, so this must not
open its own socket — a second login is not a second feed. The subscribe
path therefore delegates to the process-wide
:class:`SchwabStreamSource`, which owns the connection and fans
LEVELONE messages out to both axes.

Field IDs
---------

Verified against the Schwab Trader API Streamer Guide §3.1 and
cross-checked against ``schwab-py`` and ``Schwabdev``. **These differ
from the legacy TDA ``QUOTE`` map from field 10 onward** — under TDA,
10/11 were times-since-midnight and previous close was 15. Using a TDA
table here silently yields "previous close = exchange ID".

Field 35 is epoch **milliseconds**; :attr:`Quote.ts` is epoch seconds
(§7.7), so it is normalized on the way in.

Testability
-----------

Everything except the delegation call is pure: :func:`quote_from_levelone`
and :func:`plan_symbol_change` are unit-tested. The socket path lives in
``streaming/schwab.py`` and is ``# pragma: no cover`` there, matching the
convention established for the bar source.

See ``streaming/schwab_quotes.spec.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from ..core.timezones import normalize_epoch_to_seconds
from .quotes import Quote, QuoteCallback

LOG = logging.getLogger(__name__)

#: LEVELONE_EQUITIES numeric field IDs a quote consumer needs.
#: ``0`` symbol, ``3`` last, ``8`` total volume, ``10`` day high,
#: ``11`` day low, ``12`` **previous** close, ``35`` trade time (ms).
LEVELONE_QUOTE_FIELD_IDS = ["0", "3", "8", "10", "11", "12", "35"]

#: Practical ceiling on symbols in one streamer session. Schwab
#: documents that a limit exists (error 19 ``REACHED_SYMBOL_LIMIT``) but
#: publishes no number; 500 is the widely-reported community value.
#: Treated as advisory — we log rather than refuse, because a wrong
#: guess that blocks a legitimate subscription is worse than an error
#: message from the server.
ADVISORY_SYMBOL_LIMIT = 500


def quote_from_levelone(symbol: str, decoded: dict[str, Any]) -> Quote:
    """Build a :class:`Quote` from one decoded LEVELONE content dict.

    Absent keys stay ``None`` — Schwab sends a full image on the initial
    SUBS and **change-only deltas** afterwards, so most updates populate
    only a field or two and the consumer merges them
    (:meth:`Quote.merged_onto`). Coercing a missing field to zero here
    would overwrite a good previous close with ``0.0`` on the first
    price-only tick and blank every percent downstream.
    """

    def _f(key: str) -> float | None:
        if key not in decoded:
            return None
        try:
            value = float(decoded[key])
        except (TypeError, ValueError):
            return None
        return None if value != value else value

    ts = _f("trade_time_ms")
    return Quote(
        symbol=(symbol or "").strip().upper(),
        last=_f("last_price"),
        prev_close=_f("close_price"),
        day_volume=_f("total_volume"),
        day_high=_f("high_price"),
        day_low=_f("low_price"),
        # Schwab sends epoch milliseconds; Quote.ts is seconds (§7.7).
        ts=None if ts is None else normalize_epoch_to_seconds(ts),
    )


def plan_symbol_change(
    current: Iterable[str], desired: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Return ``(to_add, to_remove)`` for a subscription set change.

    Pure so the incremental-update decision is testable without a
    socket. Sorted for deterministic wire order (and readable logs).

    The whole reason a quote subscription exposes ``set_symbols`` rather
    than close-and-resubscribe: index membership changes by a name or
    two at a time, and tearing down 500 subscriptions to add one would
    blank the map for as long as the re-image takes.
    """
    cur = {(s or "").strip().upper() for s in current if (s or "").strip()}
    want = {(s or "").strip().upper() for s in desired if (s or "").strip()}
    return (sorted(want - cur), sorted(cur - want))


class SchwabQuoteSubscription:
    """One quote subscriber's view of the shared streamer connection.

    Holds its own symbol set and filters deliveries to it, so two
    consumers (say a heatmap and a scanner) can want overlapping but
    different universes off one socket without either seeing the
    other's symbols.
    """

    __slots__ = ("_source", "_on_quote", "symbols", "_closed")

    def __init__(self, source: Any, on_quote: QuoteCallback) -> None:
        self._source = source
        self._on_quote = on_quote
        self.symbols: set[str] = set()
        self._closed = False

    def set_symbols(self, symbols: Iterable[str]) -> None:
        if self._closed:
            return
        wanted = {
            (s or "").strip().upper() for s in symbols if (s or "").strip()
        }
        if len(wanted) > ADVISORY_SYMBOL_LIMIT:
            LOG.warning(
                "schwab-quotes: %d symbols requested, above the ~%d "
                "commonly-reported streamer ceiling; the server may "
                "reject the excess (error 19 REACHED_SYMBOL_LIMIT).",
                len(wanted), ADVISORY_SYMBOL_LIMIT,
            )
        self.symbols = wanted
        try:
            self._source._apply_quote_symbols()
        except Exception:  # noqa: BLE001
            LOG.exception("schwab-quotes: symbol reconcile failed")

    def deliver(self, quote: Quote) -> None:
        """Called from the connection thread. Filters to our symbols."""
        if self._closed or quote.symbol not in self.symbols:
            return
        self._on_quote(quote)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.symbols = set()
        try:
            self._source._drop_quote_subscription(self)
        except Exception:  # noqa: BLE001
            LOG.exception("schwab-quotes: unsubscribe failed")


class SchwabQuoteSource:
    """Quote source backed by the shared Schwab streamer connection."""

    def __init__(self, stream_source: Any | None = None) -> None:
        self._stream_source = stream_source

    def _source(self) -> Any:
        if self._stream_source is not None:
            return self._stream_source
        # Resolve the process-wide singleton lazily so importing this
        # module never constructs a connection.
        from . import STREAM_SOURCES

        return STREAM_SOURCES.get("schwab-stream")

    def subscribe_quotes(
        self, symbols: Sequence[str], on_quote: QuoteCallback
    ) -> Any:
        source = self._source()
        if source is None or not hasattr(source, "subscribe_quotes"):
            from .quotes import NullQuoteSource

            return NullQuoteSource().subscribe_quotes(symbols, on_quote)
        return source.subscribe_quotes(symbols, on_quote)


def make_source(**_kwargs: object) -> SchwabQuoteSource:
    """Registry factory."""
    return SchwabQuoteSource()


__all__ = (
    "LEVELONE_QUOTE_FIELD_IDS",
    "ADVISORY_SYMBOL_LIMIT",
    "quote_from_levelone",
    "plan_symbol_change",
    "SchwabQuoteSubscription",
    "SchwabQuoteSource",
    "make_source",
)
