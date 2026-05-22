"""Data-source protocol, type aliases, and registry helpers.

A *data source* is a plug-in that turns a ``(ticker, interval)`` pair
into a historical OHLCV series. Sources live in
``tradinglab.data.<provider>`` modules and register themselves into
:data:`DATA_SOURCES` at import time (see ``__init__`` of this package).

To add a new provider:

1. Create ``tradinglab/data/<name>_source.py`` exporting a function
   with the :data:`DataFetcher` signature.
2. Call :func:`register_source` (or add an entry to :data:`DATA_SOURCES`
   during module import).
3. Import the module from :mod:`tradinglab.data.__init__` so it's
   picked up on package load.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import Candle

# A source fetcher takes (ticker, interval) and returns candles or None
# on failure (import error, network error, empty result — all treated
# equivalently by the app).
DataFetcher = Callable[[str, str], list[Candle] | None]


# Global registry. Populated by submodules at import time. The dict
# preserves insertion order — the UI uses the first entry as the
# default selection.
DATA_SOURCES: dict[str, DataFetcher] = {}


def register_source(name: str, fetcher: DataFetcher) -> None:
    """Register a new data source under ``name``.

    Idempotent: repeat registrations overwrite. This is intentional so
    smoke tests can stub real sources by calling
    ``register_source("yfinance", fake)``.
    """
    DATA_SOURCES[name] = fetcher
