"""Streaming data sources.

Public API (re-exported for backward compatibility with the old
``tradinglab.streaming`` module)::

    StreamSource         — Protocol
    StreamCallback       — callback signature
    EventKind            — "tick" | "rollover"
    STREAM_SOURCES       — registry {name: source}
    register_stream      — imperative registration helper
    SyntheticStreamSource — offline stream for development / testing
"""

from .base import STREAM_SOURCES, EventKind, StreamCallback, StreamSource, register_stream
from .schwab import SchwabStreamSource
from .synthetic import SyntheticStreamSource

register_stream("synthetic-stream", SyntheticStreamSource())

# Register Schwab streaming only when REST credentials are present.
# The source itself will still no-op if OAuth isn't completed yet —
# this just keeps the stream-source dropdown clean for users who
# haven't configured Schwab at all. Registration is cheap (no
# network); the WS connection only opens on first subscribe.
from ..data.credentials import get_credentials as _get_credentials  # noqa: E402

if _get_credentials().schwab.is_configured():
    register_stream("schwab-stream", SchwabStreamSource())

__all__ = [
    "EventKind",
    "StreamCallback",
    "StreamSource",
    "STREAM_SOURCES",
    "register_stream",
    "SyntheticStreamSource",
    "SchwabStreamSource",
]
