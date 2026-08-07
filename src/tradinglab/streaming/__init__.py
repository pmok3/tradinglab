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

# Register Schwab streaming only when REST credentials are present.
# The source itself will still no-op if OAuth isn't completed yet —
# this just keeps the stream-source dropdown clean for users who
# haven't configured Schwab at all. Registration is cheap (no
# network); the WS connection only opens on first subscribe.
from ..data.credentials import get_credentials as _get_credentials  # noqa: E402

if _get_credentials().schwab.is_configured():
    register_stream("schwab-stream", SchwabStreamSource())
    # The quote axis rides the SAME connection (Schwab allows one
    # streamer session per user), so it is registered together with the
    # bar source and resolves the singleton lazily at subscribe time.
    from .schwab_quotes import make_source as _make_schwab_quotes  # noqa: E402

    register_quote_source("schwab-quotes", _make_schwab_quotes)

__all__ = [
    "EventKind",
    "StreamCallback",
    "StreamSource",
    "STREAM_SOURCES",
    "register_stream",
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
