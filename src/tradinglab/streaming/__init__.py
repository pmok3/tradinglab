"""Streaming data sources.

Public API (re-exported for backward compatibility with the old
``tradinglab.streaming`` module)::

    StreamSource         — Protocol
    StreamCallback       — callback signature
    EventKind            — "tick" | "rollover"
    STREAM_SOURCES       — registry {name: source}
    register_stream      — imperative registration helper
    SyntheticStreamSource — offline stream for development / testing

The *quote* axis is a sibling, not a layer on top: bar streams serve one
symbol deeply (charts), quote streams serve many symbols shallowly
(heatmap, scanner, watchlist). See ``streaming/quotes.py``::

    Quote / QuoteSource / QuoteSubscription — protocol
    QUOTE_SOURCES / register_quote_source   — registry
    QuoteBook                               — coalescing store
    SyntheticQuoteSource                    — offline quotes
"""

from .base import STREAM_SOURCES, EventKind, StreamCallback, StreamSource, register_stream
from .quote_book import QuoteBook, QuoteEntry
from .quotes import (
    QUOTE_SOURCES,
    NullQuoteSource,
    Quote,
    QuoteCallback,
    QuoteSource,
    QuoteSubscription,
    available_quote_sources,
    register_quote_source,
    resolve_quote_source,
    unregister_quote_source,
)
from .schwab import SchwabStreamSource
from .synthetic import SyntheticStreamSource
from .synthetic_quotes import SyntheticQuoteSource

register_stream("synthetic-stream", SyntheticStreamSource())
register_quote_source("synthetic-quotes", SyntheticQuoteSource)

from .registry import reconcile_vendor_streams, resolve_chart_stream  # noqa: E402

reconcile_vendor_streams()

__all__ = [
    "EventKind",
    "StreamCallback",
    "StreamSource",
    "STREAM_SOURCES",
    "register_stream",
    "reconcile_vendor_streams",
    "resolve_chart_stream",
    "SyntheticStreamSource",
    "SchwabStreamSource",
    # quote axis
    "Quote",
    "QuoteBook",
    "QuoteEntry",
    "QuoteCallback",
    "QuoteSource",
    "QuoteSubscription",
    "NullQuoteSource",
    "QUOTE_SOURCES",
    "register_quote_source",
    "unregister_quote_source",
    "available_quote_sources",
    "resolve_quote_source",
    "SyntheticQuoteSource",
]
